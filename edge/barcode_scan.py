import tkinter as tk
import urllib.request
import urllib.error
import json
import uuid

# ---------------------------------------------------------
# 1. API Integration Logic
# ---------------------------------------------------------
def process_barcode_and_send(raw_barcode, session_id="7", role="grader"):
    """Validates the barcode and sends it to the Gatekeeper API."""
    if not raw_barcode or len(raw_barcode.strip()) == 0:
        return False, "Error: No string detected."
    
    url = f"http://127.0.0.1:5000/v0/sessions/{session_id}/events"
    
    payload = {
        "barcode": raw_barcode,
        "client_ref": str(uuid.uuid4())
    }
    
    headers = {
        "Content-Type": "application/json",
        "X-Contract-Version": "v0",
        "X-Role": role
    }
    
    data = json.dumps(payload).encode('utf-8')
    
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        with urllib.request.urlopen(req) as response:
            resp_data = json.loads(response.read().decode())
            event_id = resp_data.get("event_id")
            return True, f"Success: Event {event_id} opened. Ready for next scan."
            
    except urllib.error.HTTPError as e:
        # The API rejected the request (e.g., 400, 403, 404, 409)
        error_body = e.read().decode()
        return False, f"API Error {e.code}: {error_body}"
    except urllib.error.URLError as e:
        # The API server is turned off or unreachable
        return False, "Network Error: Is the Gatekeeper API running on port 5000?"

# ---------------------------------------------------------
# 2. UI Event Handler & Window
# ---------------------------------------------------------
class MusterScannerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("MUSTER Edge Scanner (API Connected)")
        self.root.geometry("550x250")
        self.root.configure(padx=20, pady=20)

        tk.Label(root, text="Raw Scanner Input:", font=("Arial", 10, "bold")).pack(anchor="w")
        self.input_box = tk.Entry(root, width=65, font=("Arial", 12))
        self.input_box.pack(pady=(0, 15), anchor="w")
        self.input_box.focus()

        self.btn_process = tk.Button(root, text="Scan & Send to API", command=self.handle_scan, bg="#0056b3", fg="white")
        self.btn_process.pack(anchor="w", pady=(0, 20))

        tk.Label(root, text="Gatekeeper API Response:", font=("Arial", 10, "bold")).pack(anchor="w")
        self.output_box = tk.Entry(root, width=65, font=("Arial", 10), state="readonly")
        self.output_box.pack(anchor="w")

    def handle_scan(self):
        raw_input = self.input_box.get()
        
        # SECURE WIPE: Clear the input box immediately so it doesn't linger on screen
        self.input_box.delete(0, tk.END)
        self.output_box.config(state="normal")
        self.output_box.delete(0, tk.END)
        self.output_box.insert(0, "Transmitting to Gatekeeper...")
        self.root.update()

        # Send to API
        success, message = process_barcode_and_send(raw_input)
        
        # Display response
        self.output_box.delete(0, tk.END)
        self.output_box.insert(0, message)
        self.output_box.config(state="readonly")

if __name__ == "__main__":
    root = tk.Tk()
    app = MusterScannerApp(root)
    root.mainloop()