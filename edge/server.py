from flask import Flask, request, jsonify, render_template
import urllib.request
import urllib.error
import json
import uuid

app = Flask(__name__, template_folder='.')

@app.route('/')
def index():
    return render_template('scanner.html')

@app.route('/api/muster/scan', methods=['POST'])
def handle_phone_scan():
    raw_barcode = request.json.get('cac_string')
    print(f"[PHONE SCAN RECEIVED]: {raw_barcode}")
    
    # Forward the scan to Alena's Gatekeeper API
    url = "http://127.0.0.1:5000/v0/sessions/7/events"
    payload = {"barcode": raw_barcode, "client_ref": str(uuid.uuid4())}
    headers = {
        "Content-Type": "application/json",
        "X-Contract-Version": "v0",
        "X-Role": "grader"
    }
    
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
        with urllib.request.urlopen(req) as response:
            resp_data = json.loads(response.read().decode())
            print(f"[GATEKEEPER SUCCESS]: {resp_data}")
            return jsonify({"status": "success", "gatekeeper": resp_data}), 201
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"[GATEKEEPER REJECTED]: {e.code} - {error_body}")
        return jsonify({"status": "error", "code": e.code, "message": error_body}), e.code
        
    except urllib.error.URLError as e:
        print("[GATEKEEPER OFFLINE]: Is app.py running on port 5000?")
        return jsonify({"status": "error", "message": "Gatekeeper API offline"}), 503

if __name__ == '__main__':
    # Run this Edge Proxy on port 8000 to prevent crashing Alena's API
    app.run(host='0.0.0.0', port=8000)