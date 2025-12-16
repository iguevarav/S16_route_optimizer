# webhook_server.py
from flask import Flask, request, jsonify
import json
import os
from datetime import datetime

app = Flask(__name__)

# Endpoint para recibir notificaciones de n8n
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        print(f"✅ Webhook recibido: {datetime.now().isoformat()}")
        print(f"📦 Datos recibidos: {json.dumps(data, indent=2)}")
        
        # Guardar en archivo para que Streamlit pueda leerlo
        with open('webhook_data.json', 'w') as f:
            json.dump(data, f, indent=2)
        
        return jsonify({
            "status": "success",
            "message": "Webhook recibido correctamente",
            "received_at": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        print(f"❌ Error en webhook: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8501))
    print(f"🚀 Iniciando servidor webhook en puerto {port}")
    app.run(host='0.0.0.0', port=port, debug=True)