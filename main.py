import requests
from urllib.parse import quote_plus
from flask import Flask, request, jsonify
import os
from lxml import etree
from dotenv import load_dotenv
import yagmail
import logging
import atexit  # Import the atexit module
from threading import Thread, Event
import time
import signal

# Global Flask app object
app = Flask(__name__)
load_dotenv()

# Robust Configuration Handling
config = {
    'GHL_API_KEY': os.getenv('GHL_API_KEY'),
    'GHL_LOCATION_ID': os.getenv('GHL_LOCATION_ID'),
    'YOUR_GMAIL_ADDRESS': os.getenv('YOUR_GMAIL_ADDRESS'),
    'DRIVECENTRIC_IMPORT_EMAIL': os.getenv('DRIVECENTRIC_IMPORT_EMAIL'),
    'GMAIL_APP_PASSWORD': os.getenv('GMAIL_APP_PASSWORD')
}

missing_config = [key for key, value in config.items() if not value]
if missing_config:
    raise ValueError(f"Missing required environment variables: {', '.join(missing_config)}")

# Logging Setup for Debugging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def fetch_ghl_leads():
    """Fetches lead data from GoHighLevel API."""
    encoded_location_id = quote_plus(config['GHL_LOCATION_ID'])
    api_endpoint = f"https://rest.gohighlevel.com/v1/contacts?locationId={encoded_location_id}"
    headers = {"Authorization": f"Bearer {config['GHL_API_KEY']}"}
    
    try:
        response = requests.get(api_endpoint, headers=headers)
        response.raise_for_status()  # Raise exception for bad HTTP status codes
        data = response.json()
        return data.get("contacts", [])  # Handle case where "contacts" key is missing
    except requests.RequestException as e:
        logging.error(f"Error fetching GHL contacts: {e}")
        return []  # Return empty list on error

def generate_adf_xml(leads_data):
    """Generates ADF XML in VinSolutions format from GHL lead data."""
    if not leads_data:
        logging.warning("No leads found in the API response.")
        return None

    root = etree.Element("adf")
    for lead in leads_data:
        prospect = etree.SubElement(root, "prospect")

        # ID and Source 
        # (This assumes you have a source value in GHL)
        source = lead.get("Contact Source", "")
        etree.SubElement(prospect, "id", sequence="uniqueLeadId", source=source).text = str(lead.get("id", ""))

        # Request Date (Formatted for VinSolutions)
        # Adjust this if your GHL date format is different
        request_date = lead.get("createdAt", "")[:19]  # Get timestamp and trim to YYYY-MM-DDTHH:MM:SS
        etree.SubElement(prospect, "requestdate").text = request_date

        # Vehicle Information
        # (You'll need to map your GHL vehicle fields to VinSolutions format)
        vehicle_info = lead.get("Additional Info", {})  # Assuming vehicle data is in Additional Info
        vehicle = etree.SubElement(prospect, "vehicle", interest="buy", status="used")
        etree.SubElement(vehicle, "vin").text = vehicle_info.get("Vehicle Vin", "")  # Assuming you have a VIN field in GHL
        for key in ["Vehicle Year", "Vehicle Make", "Vehicle Model"]:  
            value = vehicle_info.get(key, "")
            if value:
                etree.SubElement(vehicle, key.split(" ")[1].lower()).text = str(value)  # Convert to lowercase (year, make, model)
        etree.SubElement(vehicle, "stock").text = ""  # Placeholder for stock number (if available)

        # Customer Information
        customer = etree.SubElement(prospect, "customer")
        contact = etree.SubElement(customer, "contact")
        for key in ["firstName", "lastName", "email"]:
            value = lead.get(key, "")
            if value:
                etree.SubElement(contact, "name" if key != "email" else key, 
                                part="first" if key == "firstName" else "last" if key == "lastName" else "",
                                type="individual").text = value

        # Phone Numbers (Multiple)
        phone_types = {"homePhone": "home", "cellPhone": "mobile", "workPhone": "work"}  
        for ghl_key, adf_type in phone_types.items():
            value = lead.get(ghl_key, "")
            if value:
                etree.SubElement(contact, "phone", type=adf_type).text = value

        # Comments (From AI Memory)
        comments = lead.get("AI Memory", "")  
        if comments:
            etree.SubElement(customer, "comments").text = comments

        # Vendor Information (Replace with your actual dealership information)
        vendor = etree.SubElement(prospect, "vendor")
        etree.SubElement(vendor, "vendorname").text = "Your Dealership Name"
        vendor_contact = etree.SubElement(vendor, "contact")
        etree.SubElement(vendor_contact, "name", part="full").text = "Your Dealership Name"
        etree.SubElement(vendor_contact, "email").text = "your_dealership_email@example.com"
        etree.SubElement(vendor_contact, "phone", type="business").text = "Your Dealership Phone"

        # Provider Information (Replace with VinSolutions information)
        provider = etree.SubElement(prospect, "provider")
        etree.SubElement(provider, "name", part="full").text = "VinSolutions"  # Or the actual lead provider name
        etree.SubElement(provider, "service").text = "VinSolutions Lead Service" # Or the actual service name

        # ID with Source
        source = "VERBLEAD"  # Hardcoded as VERBLEAD per your requirement
        etree.SubElement(prospect, "id", sequence="1", source=source).text = str(lead.get("id", ""))


        # Comments (Including AI Memory from ChatGPT)
        comments = lead.get("CUSTOMER", {}).get("COMMENTS", "")
        ai_memory = lead.get("Chat GPT", "")  # Assuming AI Memory is under "Chat GPT"
        if ai_memory:
            comments = f"{comments}\n\nAI Memory:\n{ai_memory}"
        if comments:
            etree.SubElement(customer, "comments").text = comments

        # Vendor Information 
        vendor_name = lead.get("VENDOR", {}).get("VENDORNAME", "")
        if vendor_name:
            vendor = etree.SubElement(prospect, "vendor")
            etree.SubElement(vendor, "vendorname").text = vendor_name

        # Provider Information (Hardcoded as VERBLEAD)
        provider = etree.SubElement(prospect, "provider")
        etree.SubElement(provider, "name", part="full").text = "VERBLEAD"
        etree.SubElement(provider, "service").text = "AI Sales"


        # Tags (Optional)
        tags = lead.get("tags", [])
        for tag in tags:
            etree.SubElement(prospect, "tag").text = tag

    return etree.tostring(root, pretty_print=True, encoding="utf-8", xml_declaration=True)


# Email Sending Function (Refactored)
def send_email(recipient, subject, contents, attachment=None):
    try:
        yag = yagmail.SMTP(config['YOUR_GMAIL_ADDRESS'], config['GMAIL_APP_PASSWORD'])
        yag.send(to=recipient, subject=subject, contents=contents, attachments=attachment)
        logging.info(f"Email sent to {recipient}")
    except Exception as e:
        logging.error(f"Error sending email: {e}")
        

# Global variable to store processed lead IDs (consider using a persistent storage like a database in production)
# Global variables
processed_leads = set()
shutdown_event = Event()

# Webhook Endpoint
@app.route('/webhook', methods=['POST'])
def handle_webhook():
    global shutdown_event
    try:
        lead_data = request.get_json()
        if not lead_data:
            return jsonify({"error": "Invalid or empty JSON payload"}), 400

        lead_id = lead_data.get("id")

        # Check for duplicate lead
        if lead_id in processed_leads:
            logging.warning(f"Duplicate lead detected: {lead_id}")
            return jsonify({"message": "Duplicate lead, ignoring"}), 200
        else:
            processed_leads.add(lead_id)

        adf_xml = generate_adf_xml([lead_data])

        if adf_xml:
            with open("lead_export.xml", "wb") as f:
                f.write(adf_xml)

            send_email(
                config['DRIVECENTRIC_IMPORT_EMAIL'],
                "New Lead from GHL",
                ["New lead in ADFXML format attached."],
                "lead_export.xml"
            )
            # Signal shutdown after processing and emailing lead
            shutdown_event.set()  

            return jsonify({"message": "Lead processed successfully"}), 200
        else:
            return jsonify({"error": "Error processing lead (no valid ADF XML generated)"}), 400

    except (ValueError, KeyError, TypeError) as e:
        logging.error(f"Webhook error: {e}, Payload: {lead_data}")
        return jsonify({"error": "Error processing lead"}), 400
    except Exception as e:
        logging.error(f"Unexpected webhook error: {e}, Payload: {lead_data}")
        return jsonify({"error": "Internal Server Error"}), 500


def raise_keyboard_interrupt():
    raise KeyboardInterrupt

def wait_and_shutdown():
    time.sleep(15)  # Adjust the timeout as needed
    raise_keyboard_interrupt()

if __name__ == "__main__":
    # Process initial leads (run only once)
    leads = fetch_ghl_leads()
    adf_xml = generate_adf_xml(leads)

    if adf_xml:
        with open("lead_export.xml", "wb") as f:
            f.write(adf_xml)
        print("ADF XML saved to lead_export.xml")

        send_email(
            config['DRIVECENTRIC_IMPORT_EMAIL'],
            "New Leads from GHL",
            ["New leads in ADFXML format attached.", "lead_export.xml"]
        )

    # Set up a signal handler to catch SIGALRM (the alarm signal)
    signal.signal(signal.SIGALRM, raise_keyboard_interrupt)

    # Set an alarm to go off in 15 seconds
    signal.alarm(15)
    
    # Start the Flask app 
    app.run(debug=False, host='0.0.0.0', port=5000)
