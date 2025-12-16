import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import requests
import json
from datetime import datetime, timedelta
import time
from supabase import create_client
from fpdf import FPDF
import io
import uuid
import folium
from streamlit_folium import folium_static
import polyline
import httpx
import asyncio
from flask import Flask, request, jsonify



# Configuración de la página
st.set_page_config(
    page_title="Delivery Trujillo - Optimización de Rutas",
    page_icon="🚚",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Configuración para Trujillo
TRUJILLO_CENTER = [-8.1092, -79.0215]

# Clases de utilidad
class SupabaseManager:
    def __init__(self):
        self.url = st.secrets["SUPABASE_URL"]
        self.key = st.secrets["SUPABASE_KEY"]
        self.client = create_client(self.url, self.key)
    
    def get_deliveries(self, filters=None):
        query = self.client.table('deliveries').select('*')
        if filters:
            for field, value in filters.items():
                query = query.eq(field, value)
        response = query.execute()
        return response.data
    
    def get_vehicles(self):
        response = self.client.table('vehicles').select('*').execute()
        return response.data
    
    def get_drivers(self):
        response = self.client.table('drivers').select('*').execute()
        return response.data
    
    def get_routes(self):
        response = self.client.table('optimized_routes').select('*').order('created_at', desc=True).execute()
        return response.data
    
    def get_route_deliveries(self, route_id=None):
        query = self.client.table('route_deliveries').select('*')
        if route_id:
            query = query.eq('route_id', route_id)
        response = query.execute()
        return response.data
    
    def get_route_with_deliveries(self, route_id):
        route_response = self.client.table('optimized_routes').select('*').eq('id', route_id).execute()
        deliveries_response = self.client.table('route_deliveries').select('*').eq('route_id', route_id).execute()
        return route_response.data[0] if route_response.data else None, deliveries_response.data
    
    def insert_delivery(self, delivery_data):
        response = self.client.table('deliveries').insert(delivery_data).execute()
        return response.data
    
    def update_delivery_status(self, delivery_id, status):
        response = self.client.table('deliveries').update({'status': status}).eq('id', delivery_id).execute()
        return response.data
    
    def create_route(self, route_data):
        response = self.client.table('optimized_routes').insert(route_data).execute()
        return response.data

class N8NIntegration:
    def __init__(self):
        self.base_url = st.secrets.get("N8N_WEBHOOK_URL", "http://localhost:5678")
        self.api_key = st.secrets.get("N8N_API_KEY", "")
    
    async def trigger_optimization(self, delivery_ids, vehicle_id=None, driver_id=None):
        """Dispara optimización manual en n8n"""
        try:
            payload = {
                "delivery_ids": delivery_ids,
                "parameters": {
                    "optimization_type": "distance",
                    "vehicle_id": vehicle_id,
                    "driver_id": driver_id,
                    "route_date": datetime.now().strftime("%Y-%m-%d"),
                    "max_waypoints": len(delivery_ids)
                },
                "metadata": {
                    "requested_by": "streamlit_ui",
                    "requested_at": datetime.now().isoformat(),
                    "location": "Trujillo, La Libertad, Perú"
                }
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {"X-API-KEY": self.api_key} if self.api_key else {}
                response = await client.post(
                    self.base_url,
                    json=payload,
                    headers=headers
                )
                
                if response.status_code == 200:
                    return {"success": True, "message": "Optimización iniciada", "data": response.json()}
                else:
                    return {"success": False, "message": f"Error {response.status_code}: {response.text}"}
                    
        except Exception as e:
            return {"success": False, "message": f"Error de conexión: {str(e)}"}
    
    def get_optimization_status(self):
        """Verifica estado de optimizaciones recientes"""
        routes = SupabaseManager().get_routes()
        if routes:
            latest = routes[0]
            return {
                "last_optimization": latest.get('created_at'),
                "total_routes": len(routes),
                "latest_route": latest.get('route_name')
            }
        return None

class MapVisualizer:
    @staticmethod
    def create_delivery_map(deliveries, route_polyline=None, center=TRUJILLO_CENTER, zoom_start=13):
        """Crea mapa interactivo con entregas y rutas"""
        m = folium.Map(location=center, zoom_start=zoom_start, tiles='cartodbpositron')
        
        # Colores por estado
        status_colors = {
            'pending': 'blue',
            'assigned': 'orange',
            'in_transit': 'purple',
            'delivered': 'green',
            'failed': 'red',
            'cancelled': 'gray'
        }
        
        # Añadir marcadores de entregas
        for delivery in deliveries:
            if delivery.get('customer_latitude') and delivery.get('customer_longitude'):
                lat = delivery['customer_latitude']
                lon = delivery['customer_longitude']
                
                color = status_colors.get(delivery['status'], 'blue')
                
                popup_content = f"""
                <div style="font-family: Arial; min-width: 250px;">
                    <h4 style="color: #1E3A8A;">📦 {delivery.get('tracking_number', 'N/A')}</h4>
                    <hr>
                    <p><strong>Cliente:</strong> {delivery.get('customer_name', 'N/A')}</p>
                    <p><strong>Dirección:</strong> {delivery.get('customer_address', 'N/A')[:40]}...</p>
                    <p><strong>Estado:</strong> <span style="color: {color};">{delivery['status'].title()}</span></p>
                    <p><strong>Prioridad:</strong> {delivery.get('priority', 'N/A')}</p>
                    <p><strong>Peso:</strong> {delivery.get('package_weight', 'N/A')} kg</p>
                </div>
                """
                
                folium.Marker(
                    [lat, lon],
                    popup=folium.Popup(popup_content, max_width=300),
                    tooltip=f"{delivery.get('tracking_number')} - {delivery.get('customer_name')}",
                    icon=folium.Icon(color=color, icon='info-sign', prefix='fa')
                ).add_to(m)
        
        # Añadir ruta si está disponible
        if route_polyline:
            try:
                decoded_polyline = polyline.decode(route_polyline)
                if decoded_polyline:
                    folium.PolyLine(
                        decoded_polyline,
                        weight=4,
                        color='#3B82F6',
                        opacity=0.8,
                        popup='Ruta Optimizada',
                        dash_array='5, 10'
                    ).add_to(m)
            except:
                pass
        
        # Añadir marcador del centro de Trujillo
        folium.Marker(
            TRUJILLO_CENTER,
            popup="<b>Centro de Trujillo</b><br>Punto de partida",
            tooltip="Centro de Trujillo",
            icon=folium.Icon(color='red', icon='flag', prefix='fa')
        ).add_to(m)
        
        return m
    
    @staticmethod
    def create_route_visualization(route, deliveries):
        """Crea visualización detallada de una ruta"""
        fig = go.Figure()
        
        # Extraer coordenadas de entregas REALES
        coords = []
        for delivery in deliveries:  # deliveries ya son objetos de entrega, no route_deliveries
            if delivery.get('customer_latitude') and delivery.get('customer_longitude'):
                coords.append((delivery['customer_latitude'], delivery['customer_longitude']))
        
        if len(coords) >= 2:
            # Crear línea de ruta
            lats, lons = zip(*coords)
            fig.add_trace(go.Scattermapbox(
                lat=lats,
                lon=lons,
                mode='lines+markers',
                line=dict(width=3, color='#3B82F6'),
                marker=dict(size=10, color='#EF4444'),
                name='Ruta',
                text=[f"Punto {i+1}" for i in range(len(coords))]
            ))
        
        fig.update_layout(
            mapbox=dict(
                style="carto-positron",
                center=dict(lat=TRUJILLO_CENTER[0], lon=TRUJILLO_CENTER[1]),
                zoom=12
            ),
            height=500,
            margin={"r":0,"t":0,"l":0,"b":0},
            showlegend=False
        )
        
        return fig
    
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    api_key = request.headers.get('X-API-KEY')
    if api_key != st.secrets["webhook"]["api_key"]:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    st.session_state.last_webhook = data
    return jsonify({"status": "received"}), 200

# Estilos CSS personalizados
def load_css():
    st.markdown("""
    <style>
    .main-header {
        background: linear-gradient(135deg, #1E3A8A 0%, #3B82F6 100%);
        color: white;
        padding: 2rem;
        border-radius: 10px;
        margin-bottom: 2rem;
        text-align: center;
    }
    .metric-card {
        background: white;
        padding: 1.5rem;
        border-radius: 10px;
        border-left: 5px solid #3B82F6;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        transition: transform 0.3s;
    }
    .metric-card:hover {
        transform: translateY(-5px);
        box-shadow: 0 10px 15px rgba(0, 0, 0, 0.1);
    }
    .delivery-card {
        background: white;
        padding: 1rem;
        border-radius: 8px;
        margin-bottom: 0.5rem;
        border: 1px solid #E5E7EB;
        transition: all 0.3s;
    }
    .delivery-card:hover {
        border-color: #3B82F6;
        box-shadow: 0 2px 4px rgba(59, 130, 246, 0.1);
    }
    .status-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 20px;
        font-size: 0.875rem;
        font-weight: 600;
    }
    .status-pending { background: #DBEAFE; color: #1E40AF; }
    .status-assigned { background: #FEF3C7; color: #92400E; }
    .status-in_transit { background: #EDE9FE; color: #5B21B6; }
    .status-delivered { background: #D1FAE5; color: #065F46; }
    .map-container {
        border-radius: 10px;
        overflow: hidden;
        border: 2px solid #E5E7EB;
        margin: 1rem 0;
    }
    .stButton > button {
        background: linear-gradient(135deg, #3B82F6 0%, #1E40AF 100%);
        color: white;
        border: none;
        padding: 0.75rem 1.5rem;
        border-radius: 8px;
        font-weight: 600;
        width: 100%;
        transition: all 0.3s;
    }
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 10px 20px rgba(59, 130, 246, 0.3);
    }
    .tab-content {
        padding: 1.5rem;
        background: white;
        border-radius: 10px;
        margin-top: 1rem;
    }
    </style>
    """, unsafe_allow_html=True)

# Funciones principales de la aplicación
def main():
    load_css()
    
    # Header principal
    st.markdown("""
    <div class="main-header">
        <h1 style="margin: 0;">🚚 Delivery Trujillo - Sistema de Optimización de Rutas</h1>
        <p style="margin: 0.5rem 0 0 0; opacity: 0.9;">La Libertad, Perú | Gestión Inteligente de Entregas</p>
    </div>
    """, unsafe_allow_html=True)
    
    # Inicializar servicios
    sb = SupabaseManager()
    n8n = N8NIntegration()
    
    # Sidebar
    with st.sidebar:
        st.markdown("### 🔍 Navegación")
        
        app_mode = st.selectbox(
            "",
            ["📊 Dashboard", "📦 Gestión de Entregas", "🗺️ Optimización de Rutas", 
             "📈 Reportes por Conductor", "📋 Historial de Rutas"]
        )
        
        st.markdown("---")
        st.markdown("### 📍 Ubicación")
        st.info("**Trujillo, La Libertad**\n\nCentro de operaciones: Plaza de Armas\nRadio de cobertura: 20km")
        
        # Estado del sistema
        status = n8n.get_optimization_status()
        if status:
            st.markdown("### ⚙️ Estado del Sistema")
            st.success(f"✅ Última optimización: {status['last_optimization'][:10]}")
            st.metric("Rutas generadas", status['total_routes'])
    
    # Navegación
    if app_mode == "📊 Dashboard":
        show_dashboard(sb)
    elif app_mode == "📦 Gestión de Entregas":
        show_delivery_management(sb)
    elif app_mode == "🗺️ Optimización de Rutas":
        show_route_optimization(sb, n8n)
    elif app_mode == "📈 Reportes por Conductor":
        show_driver_reports(sb)
    elif app_mode == "📋 Historial de Rutas":
        show_route_history(sb)

def get_district_coordinates(district):
    """Devuelve coordenadas aproximadas por distrito de Trujillo"""
    district_coords = {
        "Trujillo Centro": (-8.1092, -79.0215),
        "La Esperanza": (-8.0878, -79.0401),
        "El Porvenir": (-8.0775, -79.0169),
        "Florencia de Mora": (-8.0731, -79.0264),
        "Huanchaco": (-8.0833, -79.1167),
        "Victor Larco": (-8.1167, -79.0333),
        "Moche": (-8.1667, -79.0333),
        "Laredo": (-8.0833, -78.9667),
        "Salaverry": (-8.2167, -78.9833),
        "Poroto": (-8.0083, -78.6417)
    }
    
    return district_coords.get(district, TRUJILLO_CENTER)

def get_coordinates_from_address(address):
    """Obtiene coordenadas usando Google Maps Geocoding API (MÁS PRECISO)"""
    try:
        # 1. Obtener API key
        api_key = st.secrets.get("GOOGLE_MAPS_API_KEY", "")
        if not api_key:
            return None
        
        # 2. Asegurar formato correcto para Trujillo
        if not any(x in address for x in ["Trujillo", "La Libertad", "Perú"]):
            address = f"{address}, Trujillo, La Libertad, Perú"
        
        # 3. Llamar a Google Maps API
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            'address': address,
            'key': api_key,
            'region': 'pe',  # CLAVE: Priorizar resultados en Perú
            'language': 'es'
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        # 4. Procesar respuesta
        if data['status'] == 'OK' and data['results']:
            location = data['results'][0]['geometry']['location']
            lat = location['lat']
            lon = location['lng']
            
            # Verificar que esté cerca de Trujillo (dentro de ~50km)
            distance = ((lat - TRUJILLO_CENTER[0])**2 + (lon - TRUJILLO_CENTER[1])**2)**0.5
            if distance < 0.5:
                return (lat, lon)
        
        return None
        
    except Exception as e:
        print(f"Error en geocodificación Google: {str(e)}")
        return None

def get_coordinates_google_improved(address, api_key=None):
    """Versión mejorada de geocodificación - SÍ FUNCIONA"""
    try:
        # 1. Obtener API key de secrets
        if not api_key:
            try:
                api_key = st.secrets["GOOGLE_MAPS_API_KEY"]
            except:
                st.error("⚠️ No hay API key de Google Maps configurada")
                return None
        
        # 2. Preparar dirección para Trujillo
        # Asegurar que tenga "Trujillo, Perú" al final
        if not address.endswith(("Trujillo", "Perú", "Peru")):
            address = f"{address}, Trujillo, Perú"
        
        # 3. Llamar a Google Maps API
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            'address': address,
            'key': api_key,
            'region': 'pe',  # CRUCIAL: Prioriza resultados en Perú
            'language': 'es',
            'components': 'country:PE'
        }
        
        headers = {
            'User-Agent': 'DeliveryTrujilloApp/1.0'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        data = response.json()
        
        # 4. Procesar respuesta
        if data['status'] == 'OK' and data['results']:
            location = data['results'][0]['geometry']['location']
            lat = location['lat']
            lng = location['lng']
            
            # Verificar que esté cerca de Trujillo (dentro de 50km)
            distance = ((lat - TRUJILLO_CENTER[0])**2 + (lng - TRUJILLO_CENTER[1])**2)**0.5
            if distance < 0.5:  # Aprox 50km
                return lat, lng
            else:
                st.warning(f"⚠️ Ubicación encontrada muy lejana ({distance:.2f}°). Verifica la dirección.")
                return None
        else:
            # 5. Manejo de errores de Google
            error_msg = data.get('error_message', data['status'])
            st.warning(f"⚠️ Google Maps: {error_msg}")
            
            # Intentar una búsqueda más simple
            simple_address = address.split(',')[0] + ', Trujillo, Perú'
            params['address'] = simple_address
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            data = response.json()
            
            if data['status'] == 'OK' and data['results']:
                location = data['results'][0]['geometry']['location']
                return location['lat'], location['lng']
            else:
                return None
                
    except Exception as e:
        st.error(f"❌ Error en geocodificación: {str(e)}")
        return None
       
def get_coordinates_smart_trujillo(address, district=None):
    """Sistema inteligente para Trujillo - La Libertad"""
    
    try:
        # 1. Intentar con Google Maps primero
        api_key = st.secrets.get("GOOGLE_MAPS_API_KEY", "")
        if api_key:
            coords = get_coordinates_google_improved(address, api_key)
            if coords:
                return coords
        
        # 2. Si Google falla, usar coordenadas por distrito
        if district:
            district_coords = {
                "Trujillo Centro": (-8.1092, -79.0215),
                "La Esperanza": (-8.0878, -79.0401),
                "El Porvenir": (-8.0775, -79.0169),
                "Florencia de Mora": (-8.0731, -79.0264),
                "Huanchaco": (-8.0833, -79.1167),
                "Victor Larco": (-8.1167, -79.0333),
                "Moche": (-8.1667, -79.0333),
                "Laredo": (-8.0833, -78.9667),
                "Salaverry": (-8.2167, -78.9833),
                "Poroto": (-8.0083, -78.6417)
            }
            
            if district in district_coords:
                return district_coords[district]
        
        # 3. Último recurso: coordenadas aleatorias cerca de Trujillo
        return (
            TRUJILLO_CENTER[0] + np.random.uniform(-0.02, 0.02),
            TRUJILLO_CENTER[1] + np.random.uniform(-0.02, 0.02)
        )
        
    except:
        # 4. Fallback absoluto
        return TRUJILLO_CENTER
    

def show_dashboard(sb):
    st.header("📊 Panel de Control - Trujillo")
    
    # Obtener datos
    deliveries = sb.get_deliveries()
    vehicles = sb.get_vehicles()
    drivers = sb.get_drivers()
    routes = sb.get_routes()
    
    # Métricas principales
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        total = len(deliveries)
        st.metric("📦 Total Entregas", total)
        st.caption(f"Últimas 24h: {len([d for d in deliveries if 'created_at' in d and datetime.fromisoformat(d['created_at'].replace('Z', '+00:00')) > datetime.now() - timedelta(hours=24)])}")
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col2:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        pending = len([d for d in deliveries if d['status'] == 'pending'])
        st.metric("⏳ Pendientes", pending)
        st.caption(f"Alta prioridad: {len([d for d in deliveries if d['status'] == 'pending' and d.get('priority', 3) == 1])}")
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col3:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        in_transit = len([d for d in deliveries if d['status'] == 'in_transit'])
        st.metric("🚚 En Tránsito", in_transit)
        st.caption(f"Asignadas: {len([d for d in deliveries if d['status'] == 'assigned'])}")
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col4:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        completed = len([d for d in deliveries if d['status'] == 'delivered'])
        st.metric("✅ Completadas", completed)
        st.caption(f"Tasa éxito: {(completed/max(total,1)*100):.1f}%")
        st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Mapa de entregas
    st.subheader("🗺️ Mapa de Entregas - Trujillo")
    
    deliveries_with_coords = [d for d in deliveries if d.get('customer_latitude') and d.get('customer_longitude')]
    
    if deliveries_with_coords:
        # Crear y mostrar mapa
        m = MapVisualizer.create_delivery_map(deliveries_with_coords)
        with st.container():
            folium_static(m, width=1200, height=500)
        
        # Estadísticas del mapa
        col_info1, col_info2, col_info3 = st.columns(3)
        with col_info1:
            st.info(f"📍 {len(deliveries_with_coords)} entregas geolocalizadas")
        with col_info2:
            districts = set()
            for d in deliveries_with_coords:
                address = d.get('customer_address', '')
                if 'Trujillo' in address:
                    districts.add(address.split(',')[1].strip() if ',' in address else 'Centro')
            st.info(f"🏘️ {len(districts)} distritos cubiertos")
        with col_info3:
            if routes:
                latest = routes[0]
                st.info(f"🗺️ {len(routes)} rutas optimizadas")
    else:
        st.warning("No hay entregas con coordenadas registradas.")
    
    # Gráficos adicionales
    st.markdown("---")
    col_chart1, col_chart2 = st.columns(2)
    
    with col_chart1:
        # Distribución por estado
        if deliveries:
            status_counts = pd.Series([d['status'] for d in deliveries]).value_counts()
            fig1 = px.pie(
                values=status_counts.values,
                names=status_counts.index,
                title="Distribución por Estado",
                hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Set3
            )
            st.plotly_chart(fig1, use_container_width=True)
    
    with col_chart2:
        # Entregas por día
        if deliveries:
            delivery_dates = []
            for d in deliveries:
                if 'created_at' in d:
                    try:
                        date = datetime.fromisoformat(d['created_at'].replace('Z', '+00:00')).date()
                        delivery_dates.append(date)
                    except:
                        pass
            
            if delivery_dates:
                date_counts = pd.Series(delivery_dates).value_counts().sort_index()
                fig2 = px.bar(
                    x=date_counts.index.astype(str),
                    y=date_counts.values,
                    title="Entregas por Día",
                    labels={'x': 'Fecha', 'y': 'Cantidad'},
                    color=date_counts.values,
                    color_continuous_scale='Blues'
                )
                st.plotly_chart(fig2, use_container_width=True)

def show_delivery_management(sb):
    st.header("📦 Gestión de Entregas")
    
    # Pestañas
    tab1, tab2 = st.tabs(["➕ Nueva Entrega", "📋 Lista y Gestión"])
    
    # DISTRITOS DE TRUJILLO CON COORDENADAS PREDEFINIDAS
    TRUJILLO_DISTRICTS = {
        "Trujillo Centro": (-8.1092, -79.0215),
        "La Esperanza": (-8.0878, -79.0401),
        "El Porvenir": (-8.0775, -79.0169),
        "Florencia de Mora": (-8.0731, -79.0264),
        "Huanchaco": (-8.0833, -79.1167),
        "Victor Larco": (-8.1167, -79.0333),
        "Moche": (-8.1667, -79.0333),
        "Laredo": (-8.0833, -78.9667),
        "Salaverry": (-8.2167, -78.9833),
        "Poroto": (-8.0083, -78.6417)
    }
    
    # FUNCIÓN DE GEOCODIFICACIÓN QUE SÍ FUNCIONA
    def geocode_address_google(address, api_key=None):
        """Geocodificación usando Google Maps API - Versión MEJORADA"""
        try:
            if not api_key:
                # Obtener API key de secrets
                try:
                    api_key = st.secrets["GOOGLE_MAPS_API_KEY"]
                except:
                    return None
            
            # Asegurar que la dirección tenga formato correcto
            if not any(x in address for x in ["Trujillo", "La Libertad", "Perú", "Peru"]):
                address = f"{address}, Trujillo, La Libertad, Perú"
            
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                'address': address,
                'key': api_key,
                'region': 'pe',  # CLAVE: Priorizar resultados en Perú
                'language': 'es',
                'components': 'country:PE'
            }
            
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if data['status'] == 'OK' and data['results']:
                location = data['results'][0]['geometry']['location']
                return location['lat'], location['lng']
            
            return None
            
        except Exception as e:
            print(f"Error en geocodificación Google: {str(e)}")
            return None
    
    def get_coordinates_smart(address, district=None):
        """Sistema inteligente: primero Google, luego distrito, luego aleatorio"""
        # 1. Intentar con Google Maps
        coords = geocode_address_google(address)
        
        if coords:
            # Verificar que las coordenadas estén cerca de Trujillo
            lat, lng = coords
            distance_from_center = ((lat - TRUJILLO_CENTER[0])**2 + (lng - TRUJILLO_CENTER[1])**2)**0.5
            
            if distance_from_center < 0.5:  # Menos de ~50km de Trujillo
                return coords
        
        # 2. Si Google falla o está muy lejos, usar coordenadas del distrito
        if district and district in TRUJILLO_DISTRICTS:
            return TRUJILLO_DISTRICTS[district]
        
        # 3. Si no hay distrito o no está en la lista, usar coordenadas aleatorias cerca de Trujillo
        return (
            TRUJILLO_CENTER[0] + np.random.uniform(-0.02, 0.02),
            TRUJILLO_CENTER[1] + np.random.uniform(-0.02, 0.02)
        )
    
    with tab1:
        st.subheader("Crear Nueva Entrega en Trujillo")
        
        # Sección de geocodificación previa (fuera del formulario)
        st.info("📍 **Paso 1: Preparar la dirección**")
        
        col_addr1, col_addr2, col_addr3 = st.columns([3, 2, 1])
        
        with col_addr1:
            street = st.text_input("Calle y Número *", 
                                 placeholder="Ej: San Andrés 457",
                                 key="street_input")
        
        with col_addr2:
            urbanizacion = st.text_input("Urbanización/Zona", 
                                       placeholder="Ej: Moche 13610",
                                       key="urb_input")
        
        with col_addr3:
            # Distrito seleccionable
            district = st.selectbox("Distrito *", 
                                  list(TRUJILLO_DISTRICTS.keys()),
                                  key="district_select")
        
        # Mostrar coordenadas del distrito seleccionado
        if district in TRUJILLO_DISTRICTS:
            dist_lat, dist_lon = TRUJILLO_DISTRICTS[district]
            st.caption(f"📍 Coordenadas del distrito **{district}**: {dist_lat:.6f}, {dist_lon:.6f}")
        
        # Botón para probar geocodificación (FUERA del formulario)
        if st.button("🔍 Probar geocodificación de la dirección", type="secondary"):
            if street and district:
                # Construir dirección de prueba
                test_address = f"{street}"
                if urbanizacion:
                    test_address += f", {urbanizacion}"
                test_address += f", {district}, Trujillo, La Libertad, Perú"
                
                with st.spinner("Buscando ubicación exacta..."):
                    coords = get_coordinates_smart(test_address, district)
                    
                    if coords:
                        lat, lon = coords
                        st.success(f"✅ Ubicación encontrada: {lat:.6f}, {lon:.6f}")
                        
                        # Verificar si son las del distrito o más precisas
                        if district in TRUJILLO_DISTRICTS:
                            dist_lat, dist_lon = TRUJILLO_DISTRICTS[district]
                            if abs(lat - dist_lat) > 0.001 or abs(lon - dist_lon) > 0.001:
                                st.info("📍 Se encontró una ubicación más precisa que la del distrito")
                            else:
                                st.info("📍 Usando coordenadas del distrito (no se encontró ubicación más precisa)")
                        
                        # Guardar en session state para el formulario
                        st.session_state['pre_geocoded'] = {
                            'address': test_address,
                            'latitude': lat,
                            'longitude': lon,
                            'district': district
                        }
                                            
                        m = folium.Map(location=[lat, lon], zoom_start=16)
                        folium.Marker(
                            [lat, lon],
                            popup=test_address,
                            tooltip="Ubicación encontrada",
                            icon=folium.Icon(color='green', icon='home', prefix='fa')
                        ).add_to(m)
                        
                        # Añadir marcador del centro del distrito si es diferente
                        if district in TRUJILLO_DISTRICTS:
                            dist_lat, dist_lon = TRUJILLO_DISTRICTS[district]
                            if abs(lat - dist_lat) > 0.001 or abs(lon - dist_lon) > 0.001:
                                folium.Marker(
                                    [dist_lat, dist_lon],
                                    popup=f"Centro de {district}",
                                    tooltip="Ubicación del distrito",
                                    icon=folium.Icon(color='blue', icon='flag', prefix='fa')
                                ).add_to(m)
                        
                        folium_static(m, width=600, height=400)
            else:
                st.warning("⚠️ Completa al menos la calle y selecciona un distrito")
        
        st.markdown("---")
        
        # FORMULARIO PRINCIPAL (separado del botón de geocodificación)
        with st.form("nueva_entrega_form"):
            st.subheader("Paso 2: Completar información de la entrega")
            
            col1, col2 = st.columns(2)
            
            with col1:
                customer_name = st.text_input("Nombre del Cliente *", 
                                             placeholder="Ej: Juan Pérez López")
                customer_phone = st.text_input("Teléfono *", 
                                              placeholder="Ej: 044 123456")
                customer_email = st.text_input("Email", 
                                              placeholder="cliente@email.com")
            
            with col2:
                package_description = st.text_area("Descripción del Paquete",
                                                  placeholder="Contenido, cuidados especiales")
                package_weight = st.number_input("Peso (kg) *", min_value=0.1, step=0.1, value=1.0)
                
                priority = st.select_slider("Prioridad",
                                           options=[1, 2, 3, 4, 5],
                                           value=3,
                                           help="1 = Muy urgente, 5 = Normal")
                
                special_instructions = st.text_area("Instrucciones Especiales",
                                                   placeholder="Ej: Llamar antes de llegar, código de acceso")
            
            # Checkbox para usar geocodificación precisa
            use_precise_geocoding = st.checkbox(
                "📍 Usar geocodificación precisa (Google Maps)", 
                value=('pre_geocoded' in st.session_state),
                help="Si está desmarcado, se usarán las coordenadas del distrito"
            )
            
            submitted = st.form_submit_button("✨ Crear Entrega", type="primary")
            
            if submitted:
                # VALIDACIONES
                if not all([customer_name, customer_phone, street, district]):
                    st.error("❌ Por favor completa todos los campos obligatorios (*)")
                    return
                
                # CONSTRUIR DIRECCIÓN COMPLETA
                address_parts = [street]
                if urbanizacion:
                    address_parts.append(urbanizacion)
                address_parts.extend([district, "Trujillo", "La Libertad", "Perú"])
                full_address = ", ".join(address_parts)
                
                # OBTENER COORDENADAS
                if use_precise_geocoding and 'pre_geocoded' in st.session_state:
                    # Usar coordenadas ya geocodificadas (del botón previo)
                    latitude = st.session_state['pre_geocoded']['latitude']
                    longitude = st.session_state['pre_geocoded']['longitude']
                    st.success("✅ Usando coordenadas geocodificadas previamente")
                else:
                    # Usar sistema inteligente de coordenadas
                    with st.spinner("📍 Obteniendo coordenadas..."):
                        coords = get_coordinates_smart(full_address, district)
                        
                        if coords:
                            latitude, longitude = coords
                            st.success(f"✅ Coordenadas asignadas: {latitude:.6f}, {longitude:.6f}")
                        else:
                            # Fallback absoluto
                            latitude, longitude = TRUJILLO_DISTRICTS.get(district, TRUJILLO_CENTER)
                            st.warning("⚠️ No se pudieron obtener coordenadas. Usando ubicación del distrito")
                
                # CREAR OBJETO DE ENTREGA
                new_delivery = {
                    'tracking_number': f"TRU{datetime.now().strftime('%y%m%d')}{np.random.randint(1000, 9999)}",
                    'customer_name': customer_name,
                    'customer_email': customer_email if customer_email else None,
                    'customer_phone': customer_phone,
                    'customer_address': full_address,
                    'customer_latitude': float(latitude),
                    'customer_longitude': float(longitude),
                    'package_description': package_description if package_description else None,
                    'package_weight': float(package_weight),
                    'priority': int(priority),
                    'status': 'pending',
                    'created_at': datetime.now().isoformat(),
                }
                
                if special_instructions:
                    new_delivery['special_instructions'] = special_instructions
                
                # GUARDAR EN LA BASE DE DATOS
                try:
                    result = sb.insert_delivery(new_delivery)
                    if result:
                        st.success(f"✅ Entrega creada exitosamente!")
                        st.info(f"**Número de tracking:** `{new_delivery['tracking_number']}`")
                        st.info(f"**Dirección:** {full_address[:80]}...")
                        st.info(f"**Coordenadas:** {latitude:.6f}, {longitude:.6f}")
                        
                        # Limpiar session state para próxima entrega
                        if 'pre_geocoded' in st.session_state:
                            del st.session_state['pre_geocoded']
                        if 'street_input' in st.session_state:
                            del st.session_state.street_input
                        if 'urb_input' in st.session_state:
                            del st.session_state.urb_input
                        if 'district_select' in st.session_state:
                            del st.session_state.district_select
                        
                        st.balloons()
                        
                        # Auto-refrescar después de 2 segundos
                        time.sleep(2)
                        st.rerun()
                        
                except Exception as e:
                    st.error(f"❌ Error al crear la entrega: {str(e)}")
    
    with tab2:
        st.subheader("📋 Lista y Gestión de Entregas")
        
        # Obtener todas las entregas
        deliveries = sb.get_deliveries()
        
        if not deliveries:
            st.info("📭 No hay entregas registradas en el sistema.")
            return
        
        # Convertir a DataFrame para filtros
        df = pd.DataFrame(deliveries)
        
        # Filtros avanzados
        st.subheader("🔍 Filtros de Búsqueda")
        
        col_f1, col_f2, col_f3, col_f4 = st.columns(4)
        
        with col_f1:
            # Filtro por estado
            status_options = ["Todos"] + sorted(df['status'].unique().tolist())
            status_filter = st.selectbox("Estado", status_options)
        
        with col_f2:
            # Filtro por prioridad
            priority_options = ["Todas"] + [str(i) for i in range(1, 6)]
            priority_filter = st.selectbox("Prioridad", priority_options)
        
        with col_f3:
            # Filtro por distrito (si existe la columna)
            if 'district' in df.columns and df['district'].notna().any():
                district_options = ["Todos"] + sorted(df['district'].dropna().unique().tolist())
                district_filter = st.selectbox("Distrito", district_options)
            else:
                district_filter = "Todos"
                st.caption("Distrito no disponible")
        
        with col_f4:
            # Búsqueda por texto
            search_text = st.text_input("Buscar (cliente/tracking)")
        
        # Aplicar filtros
        filtered_df = df.copy()
        
        if status_filter != "Todos":
            filtered_df = filtered_df[filtered_df['status'] == status_filter]
        
        if priority_filter != "Todas":
            filtered_df = filtered_df[filtered_df['priority'] == int(priority_filter)]
        
        if district_filter != "Todos" and 'district' in filtered_df.columns:
            filtered_df = filtered_df[filtered_df['district'] == district_filter]
        
        if search_text:
            mask = (
                filtered_df['customer_name'].str.contains(search_text, case=False, na=False) |
                filtered_df['tracking_number'].str.contains(search_text, case=False, na=False) |
                filtered_df['customer_address'].str.contains(search_text, case=False, na=False)
            )
            filtered_df = filtered_df[mask]
        
        # Mostrar resultados
        st.subheader(f"📊 Resultados ({len(filtered_df)} entregas)")
        
        if not filtered_df.empty:
            # Seleccionar columnas para mostrar
            display_columns = ['tracking_number', 'customer_name', 'status', 'priority', 
                             'customer_address', 'package_weight', 'created_at']
            
            # Asegurar que las columnas existan
            available_columns = [col for col in display_columns if col in filtered_df.columns]
            
            # Mostrar tabla
            st.dataframe(
                filtered_df[available_columns],
                use_container_width=True,
                height=400
            )
            
            # Estadísticas rápidas
            col_stat1, col_stat2, col_stat3 = st.columns(3)
            with col_stat1:
                st.metric("📦 Total filtrado", len(filtered_df))
            with col_stat2:
                avg_priority = filtered_df['priority'].mean() if 'priority' in filtered_df.columns else 0
                st.metric("🎯 Prioridad media", f"{avg_priority:.1f}")
            with col_stat3:
                total_weight = filtered_df['package_weight'].sum() if 'package_weight' in filtered_df.columns else 0
                st.metric("⚖️ Peso total", f"{total_weight:.1f} kg")
            
            # ACCIONES POR LOTE
            st.subheader("⚡ Acciones en Lote")
            
            selected_trackings = st.multiselect(
                "Seleccionar entregas por número de tracking:",
                options=filtered_df['tracking_number'].tolist(),
                help="Ctrl+clic para seleccionar múltiples"
            )
            
            if selected_trackings:
                selected_rows = filtered_df[filtered_df['tracking_number'].isin(selected_trackings)]
                
                st.info(f"✅ {len(selected_rows)} entregas seleccionadas")
                
                col_act1, col_act2, col_act3, col_act4 = st.columns(4)
                
                with col_act1:
                    if st.button("📝 Marcar como 'Asignada'", use_container_width=True):
                        for _, row in selected_rows.iterrows():
                            sb.update_delivery_status(row['id'], 'assigned')
                        st.success(f"{len(selected_rows)} entregas asignadas")
                        time.sleep(1)
                        st.rerun()
                
                with col_act2:
                    if st.button("🚚 Marcar como 'En Tránsito'", use_container_width=True):
                        for _, row in selected_rows.iterrows():
                            sb.update_delivery_status(row['id'], 'in_transit')
                        st.success(f"{len(selected_rows)} entregas en tránsito")
                        time.sleep(1)
                        st.rerun()
                
                with col_act3:
                    if st.button("✅ Marcar como 'Entregada'", use_container_width=True):
                        for _, row in selected_rows.iterrows():
                            sb.update_delivery_status(row['id'], 'delivered')
                        st.success(f"{len(selected_rows)} entregas completadas")
                        time.sleep(1)
                        st.rerun()
                
                with col_act4:
                    if st.button("🗑️ Eliminar seleccionadas", type="secondary", use_container_width=True):
                        st.warning("⚠️ Función de eliminación en desarrollo")
                        st.info("Por ahora, cambia el estado a 'cancelled'")
                        
                        for _, row in selected_rows.iterrows():
                            sb.update_delivery_status(row['id'], 'cancelled')
                        
                        st.success(f"{len(selected_rows)} entregas canceladas")
                        time.sleep(1)
                        st.rerun()
                
                # Mostrar detalles de las seleccionadas
                with st.expander("📋 Ver detalles de las entregas seleccionadas"):
                    for _, row in selected_rows.iterrows():
                        st.write(f"**{row['tracking_number']}** - {row['customer_name']}")
                        st.write(f"📍 {row.get('customer_address', 'Sin dirección')[:60]}...")
                        st.write(f"📦 Peso: {row.get('package_weight', 'N/A')} kg | Prioridad: {row.get('priority', 'N/A')}")
                        st.divider()
            
            # Exportar datos
            st.subheader("💾 Exportar Datos")
            
            col_exp1, col_exp2 = st.columns(2)
            
            with col_exp1:
                # CSV
                csv = filtered_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Descargar como CSV",
                    data=csv,
                    file_name=f"entregas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            
            with col_exp2:
                # JSON
                json_data = filtered_df.to_json(orient='records', indent=2)
                st.download_button(
                    label="📥 Descargar como JSON",
                    data=json_data,
                    file_name=f"entregas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json",
                    use_container_width=True
                )
        else:
            st.warning("⚠️ No hay entregas que coincidan con los filtros")
            
            # Botón para limpiar filtros
            if st.button("🧹 Limpiar todos los filtros"):
                st.session_state.clear()
                st.rerun()

def show_route_details(sb, route_id):
    """Muestra los detalles de una ruta específica"""
    route, deliveries = sb.get_route_with_deliveries(route_id)
    
    if route:
        st.subheader(f"🗺️ Ruta: {route.get('route_name')}")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Distancia", f"{route.get('total_distance_km', 0)} km")
        with col2:
            st.metric("Duración", f"{route.get('estimated_duration_minutes', 0)} min")
        with col3:
            st.metric("Entregas", route.get('metadata', {}).get('delivery_count', 0))
        
        # Mapa
        if deliveries:
            # Obtener datos completos de las entregas
            delivery_data = []
            for rd in deliveries:
                try:
                    delivery = sb.client.table('deliveries').select('*').eq('id', rd['delivery_id']).single().execute()
                    if delivery.data:
                        delivery_data.append(delivery.data)
                except:
                    pass
            
            if delivery_data:
                # Mostrar mapa
                st.subheader("📍 Mapa de la Ruta")
                route_map = MapVisualizer.create_delivery_map(
                    delivery_data,
                    route.get('polyline')
                )
                folium_static(route_map, width=1200, height=500)
                
                # Tabla de entregas
                st.subheader("📦 Entregas en esta ruta")
                df_deliveries = pd.DataFrame(delivery_data)
                st.dataframe(df_deliveries[['tracking_number', 'customer_name', 'customer_address', 'status']])

def show_route_optimization(sb, n8n):
    st.header("🗺️ Optimización de Rutas con Backend n8n")
    
    st.info("""
    ⚡ **Esta función envía solicitudes al backend (n8n) para optimización automática usando Google Maps API**
    
    **Proceso:**
    1. Seleccionas entregas pendientes
    2. Se envía solicitud a n8n via webhook
    3. n8n optimiza usando Google Maps
    4. Resultados se guardan automáticamente
    5. Se notifica a conductores y clientes
    """)
    
    # Obtener entregas pendientes con coordenadas
    deliveries = sb.get_deliveries({'status': 'pending'})
    deliveries_with_coords = [d for d in deliveries if d.get('customer_latitude') and d.get('customer_longitude')]
    
    if not deliveries_with_coords:
        st.warning("📭 No hay entregas pendientes con coordenadas para optimizar.")
        st.info("""
        **Solución:**
        1. Asegúrate que las entregas tengan coordenadas geográficas
        2. Verifica que el estado sea 'pending'
        3. Puedes añadir coordenadas en la sección de gestión de entregas
        """)
        return
    
    # Mostrar entregas disponibles
    st.subheader("1. Entregas Disponibles para Optimización")
    
    delivery_options = {
        f"{d['tracking_number']} - {d['customer_name']} - {d.get('customer_address', '')[:30]}...": d['id'] 
        for d in deliveries_with_coords
    }
    
    selected_deliveries = st.multiselect(
        "Selecciona entregas para incluir en la ruta:",
        options=list(delivery_options.keys()),
        help="Máximo 25 entregas por solicitud (límite de Google Maps API)"
    )
    
    if not selected_deliveries:
        st.warning("Selecciona al menos una entrega para continuar")
        return
    
    selected_ids = [delivery_options[d] for d in selected_deliveries]
    
    # Mostrar vista previa del mapa
    st.subheader("📍 Vista Previa - Ubicaciones Seleccionadas")
    
    selected_delivery_data = [d for d in deliveries_with_coords if d['id'] in selected_ids]
    preview_map = MapVisualizer.create_delivery_map(selected_delivery_data)
    
    with st.container():
        folium_static(preview_map, width=1200, height=400)
    
    # Configuración de optimización
    st.subheader("2. Configuración de la Ruta")
    
    col_config1, col_config2 = st.columns(2)
    
    with col_config1:
        # Vehículos disponibles
        vehicles = sb.get_vehicles()
        available_vehicles = [v for v in vehicles if v.get('status') == 'available']
        
        if available_vehicles:
            vehicle_options = {f"{v['license_plate']} ({v['vehicle_type']})": v['id'] 
                             for v in available_vehicles}
            selected_vehicle = st.selectbox(
                "Vehículo para la ruta:",
                options=list(vehicle_options.keys())
            )
            vehicle_id = vehicle_options[selected_vehicle]
        else:
            st.warning("No hay vehículos disponibles")
            vehicle_id = None
    
    with col_config2:
        # Conductores disponibles
        drivers = sb.get_drivers()
        available_drivers = [d for d in drivers if d.get('status') == 'available']
        
        if available_drivers:
            driver_options = {f"{d['name']} - {d.get('license_number', 'Sin licencia')}": d['id'] 
                            for d in available_drivers}
            selected_driver = st.selectbox(
                "Conductor asignado:",
                options=list(driver_options.keys())
            )
            driver_id = driver_options[selected_driver]
        else:
            st.warning("No hay conductores disponibles")
            driver_id = None
    
    # Botón de optimización
    st.subheader("3. Solicitar Optimización")
    
    if st.button("🚀 Solicitar Optimización al Backend (n8n)", type="primary", use_container_width=True):
        if len(selected_ids) > 25:
            st.error("❌ Máximo 25 entregas por solicitud (límite de Google Maps API)")
            return
        
        # Mostrar progreso
        
        with st.spinner("⏳ Enviando solicitud al backend..."):
            # Simplemente ejecuta el webhook manual de n8n
            webhook_url = "http://localhost:5678/webhook-test/manual-optimization"
            try:
                response = requests.post(webhook_url, timeout=30)
                if response.status_code == 200:
                    st.success("✅ Optimización iniciada!")
                    
                    # **CAMBIO AQUÍ**: En lugar de esperar webhook, consulta Supabase
                    st.info("⏳ Verificando resultados en Supabase...")
                    
                    # Espera 5 segundos y luego verifica
                    time.sleep(5)
                    
                    # Verifica si se creó una nueva ruta
                    routes = sb.get_routes()
                    if routes:
                        latest_route = routes[0]
                        st.success(f"✅ Ruta creada: {latest_route['route_name']}")
                        st.json({
                            "distancia": f"{latest_route.get('total_distance_km', 0)} km",
                            "duración": f"{latest_route.get('estimated_duration_minutes', 0)} min",
                            "entregas": latest_route.get('metadata', {}).get('delivery_count', 0)
                        })
                        
                        # Muestra botón para ver la ruta
                        if st.button("🗺️ Ver Ruta Optimizada"):
                            show_route_details(sb, latest_route['id'])
                else:
                    st.error(f"❌ Error del backend: {response.status_code}")
                    
            except Exception as e:
                st.error(f"❌ Error de conexión: {str(e)}")
                st.info("""
                **Solución rápida:**
                1. Asegúrate que n8n esté corriendo
                2. Ejecuta manualmente el workflow en n8n
                3. Los datos se guardarán en Supabase automáticamente
                """)
        

def show_driver_reports(sb):
    st.header("👥 Reportes por Conductor")
    
    drivers = sb.get_drivers()
    deliveries = sb.get_deliveries()
    
    if not drivers:
        st.warning("No hay conductores registrados.")
        return
    
    # Selección de conductor
    driver_options = {f"{d['name']} ({d.get('license_number', 'Sin licencia')})": d['id'] 
                     for d in drivers}
    
    selected_driver = st.selectbox(
        "Seleccionar conductor:",
        options=list(driver_options.keys())
    )
    
    if not selected_driver:
        return
    
    driver_id = driver_options[selected_driver]
    driver = next((d for d in drivers if d['id'] == driver_id), None)
    
    if not driver:
        return
    
    # Información del conductor
    st.subheader(f"📊 Estadísticas de {driver['name']}")
    
    col_d1, col_d2, col_d3, col_d4 = st.columns(4)
    
    driver_deliveries = [d for d in deliveries if d.get('assigned_driver_id') == driver_id]
    
    with col_d1:
        total = len(driver_deliveries)
        st.metric("Total Entregas", total)
    
    with col_d2:
        completed = len([d for d in driver_deliveries if d['status'] == 'delivered'])
        st.metric("Completadas", completed)
    
    with col_d3:
        pending = len([d for d in driver_deliveries if d['status'] in ['pending', 'assigned']])
        st.metric("Pendientes", pending)
    
    with col_d4:
        efficiency = (completed / max(total, 1)) * 100
        st.metric("Eficiencia", f"{efficiency:.1f}%")
    
    st.markdown("---")
    
    # Entregas asignadas al conductor
    st.subheader(f"📦 Entregas Asignadas a {driver['name']}")
    
    if driver_deliveries:
        # Filtrar por estado
        status_filter = st.selectbox("Filtrar por estado:",
                                   ["Todas", "pending", "assigned", "in_transit", "delivered"])
        
        filtered_deliveries = driver_deliveries
        if status_filter != "Todas":
            filtered_deliveries = [d for d in driver_deliveries if d['status'] == status_filter]
        
        if filtered_deliveries:
            # Crear tabla
            df_driver = pd.DataFrame(filtered_deliveries)
            display_cols = ['tracking_number', 'customer_name', 'status', 
                          'priority', 'package_weight', 'created_at']
            
            st.dataframe(
                df_driver[display_cols],
                use_container_width=True,
                height=300
            )
            
            # Mapa de entregas del conductor
            st.subheader(f"🗺️ Mapa de Entregas - {driver['name']}")
            
            deliveries_with_coords = [d for d in filtered_deliveries 
                                     if d.get('customer_latitude') and d.get('customer_longitude')]
            
            if deliveries_with_coords:
                driver_map = MapVisualizer.create_delivery_map(deliveries_with_coords)
                folium_static(driver_map, width=1200, height=500)
            else:
                st.info("No hay entregas con coordenadas para mostrar en el mapa.")
            
            # Gráfico de desempeño
            st.subheader("📈 Desempeño del Conductor")
            
            if len(driver_deliveries) > 1:
                # Preparar datos para gráfico
                performance_data = []
                for delivery in driver_deliveries:
                    if 'created_at' in delivery:
                        date = datetime.fromisoformat(delivery['created_at'].replace('Z', '+00:00')).date()
                        performance_data.append({
                            'date': date,
                            'status': delivery['status'],
                            'priority': delivery.get('priority', 3)
                        })
                
                if performance_data:
                    df_perf = pd.DataFrame(performance_data)
                    
                    # Entregas por día
                    daily_counts = df_perf.groupby('date').size().reset_index(name='count')
                    fig1 = px.line(daily_counts, x='date', y='count',
                                  title="Entregas por Día",
                                  markers=True)
                    st.plotly_chart(fig1, use_container_width=True)
        else:
            st.info(f"No hay entregas con estado '{status_filter}' para este conductor.")
    else:
        st.info(f"{driver['name']} no tiene entregas asignadas.")

def show_route_history(sb):
    st.header("📋 Historial de Rutas Optimizadas")
    
    routes = sb.get_routes()
    
    if not routes:
        st.info("No hay rutas optimizadas registradas.")
        return
    
    # Filtro por fecha
    st.subheader("Filtrar Rutas")
    
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        start_date = st.date_input("Desde", datetime.now() - timedelta(days=30))
    with col_f2:
        end_date = st.date_input("Hasta", datetime.now())
    
    # Filtrar rutas por fecha
    filtered_routes = []
    for route in routes:
        try:
            route_date = datetime.fromisoformat(route['created_at'].replace('Z', '+00:00')).date()
            if start_date <= route_date <= end_date:
                filtered_routes.append(route)
        except:
            continue
    
    if not filtered_routes:
        st.warning("No hay rutas en el período seleccionado.")
        return
    
    # Mostrar lista de rutas
    st.subheader(f"📅 Rutas Optimizadas ({len(filtered_routes)})")
    
    for route in filtered_routes[:10]:  # Mostrar máximo 10
        with st.expander(f"🗺️ {route.get('route_name', 'Ruta sin nombre')} - {route.get('created_at', '')[:10]}"):
            col_r1, col_r2, col_r3 = st.columns(3)
            
            with col_r1:
                st.metric("Distancia", f"{route.get('total_distance_km', 0):.1f} km")
            with col_r2:
                st.metric("Duración", f"{route.get('estimated_duration_minutes', 0):.0f} min")
            with col_r3:
                st.metric("Estado", route.get('route_status', 'desconocido').title())
            
            # Obtener entregas de esta ruta
            route_deliveries = sb.get_route_deliveries(route['id'])
            
            if route_deliveries:
                st.write("**Entregas en esta ruta:**")
                for rd in route_deliveries:
                    st.write(f"- Orden {rd.get('sequence_order')}: Entrega {rd.get('delivery_id', '')[:8]}...")
            
            # Botones de acción
            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                if st.button("👁️ Ver Detalles", key=f"view_{route['id']}"):
                    st.session_state.selected_route = route['id']
            with col_btn2:
                if st.button("🗺️ Ver en Mapa", key=f"map_{route['id']}"):
                    # Mostrar mapa de esta ruta
                    if route.get('polyline'):
                        try:
                            # Obtener entregas para mostrar en mapa
                            deliveries_data = []
                            for rd in route_deliveries:
                                try:
                                    response = sb.client.table('deliveries').select('*').eq('id', rd['delivery_id']).single().execute()
                                    if response.data:
                                        deliveries_data.append(response.data)
                                except:
                                    pass
                            
                            if deliveries_data:
                                route_map = MapVisualizer.create_delivery_map(
                                    deliveries_data, 
                                    route['polyline']
                                )
                                folium_static(route_map, width=800, height=500)
                        except:
                            st.error("Error al mostrar el mapa")
    
    # Gráfico de rutas por día
    st.markdown("---")
    st.subheader("📈 Estadísticas de Rutas")
    
    if len(filtered_routes) > 1:
        # Preparar datos
        route_dates = []
        for route in filtered_routes:
            try:
                date = datetime.fromisoformat(route['created_at'].replace('Z', '+00:00')).date()
                route_dates.append(date)
            except:
                pass
        
        if route_dates:
            date_counts = pd.Series(route_dates).value_counts().sort_index()
            fig = px.bar(
                x=date_counts.index.astype(str),
                y=date_counts.values,
                title="Rutas Optimizadas por Día",
                labels={'x': 'Fecha', 'y': 'Cantidad de Rutas'},
                color=date_counts.values,
                color_continuous_scale='Viridis'
            )
            st.plotly_chart(fig, use_container_width=True)

if __name__ == "__main__":
    main()