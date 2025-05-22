from flask import Flask, request, send_file
from twilio.twiml.messaging_response import MessagingResponse
import sqlite3
import os
from datetime import datetime
import requests
from werkzeug.utils import secure_filename
import requests
from base64 import b64encode
from dotenv import load_dotenv

load_dotenv()


# Your Twilio credentials
account_sid = os.environ.get('TWILIO_ACCOUNT_SID', 'your_account_sid')
auth_token = os.environ.get('TWILIO_AUTH_TOKEN', 'your_auth_token')

# Prepare the Basic Authentication header
auth_str = f"{account_sid}:{auth_token}"
auth_bytes = auth_str.encode('utf-8')
auth_b64 = b64encode(auth_bytes).decode('utf-8')
headers = {'Authorization': f'Basic {auth_b64}'}

app = Flask(__name__)

# Configure upload folder for pothole images
UPLOAD_FOLDER = 'pothole_images'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect('potholes.db')
    c = conn.cursor()
    c.execute('''
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY,
        phone_number TEXT,
        description TEXT,
        latitude REAL,
        longitude REAL,
        image_path TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''
    CREATE TABLE IF NOT EXISTS conversations (
        phone_number TEXT PRIMARY KEY,
        state TEXT,
        description TEXT,
        latitude REAL,
        longitude REAL
    )''')
    conn.commit()
    conn.close()

init_db()

# Conversation states
DESCRIBE_LOCATION = "describe_location"
SHARE_LOCATION = "share_location"
SHARE_PHOTO = "share_photo"
COMPLETE = "complete"

# Helper function to get or create conversation state
def get_conversation_state(phone_number):
    conn = sqlite3.connect('potholes.db')
    c = conn.cursor()
    c.execute("SELECT state FROM conversations WHERE phone_number = ?", (phone_number,))
    result = c.fetchone()
    
    if result is None:
        # Initialize new conversation
        c.execute("INSERT INTO conversations (phone_number, state) VALUES (?, ?)", 
                 (phone_number, DESCRIBE_LOCATION))
        conn.commit()
        state = DESCRIBE_LOCATION
    else:
        state = result[0]
    
    conn.close()
    return state

# Update conversation state
def update_conversation_state(phone_number, state):
    conn = sqlite3.connect('potholes.db')
    c = conn.cursor()
    c.execute("UPDATE conversations SET state = ? WHERE phone_number = ?", 
             (state, phone_number))
    conn.commit()
    conn.close()

# Save description to conversation
def save_description(phone_number, description):
    conn = sqlite3.connect('potholes.db')
    c = conn.cursor()
    c.execute("UPDATE conversations SET description = ? WHERE phone_number = ?", 
             (description, phone_number))
    conn.commit()
    conn.close()

# Save location to conversation
def save_location(phone_number, latitude, longitude):
    conn = sqlite3.connect('potholes.db')
    c = conn.cursor()
    c.execute("UPDATE conversations SET latitude = ?, longitude = ? WHERE phone_number = ?", 
             (latitude, longitude, phone_number))
    conn.commit()
    conn.close()

# Save final report to database
def save_report(phone_number, image_path):
    conn = sqlite3.connect('potholes.db')
    c = conn.cursor()
    
    # Get conversation data
    c.execute("SELECT description, latitude, longitude FROM conversations WHERE phone_number = ?", 
             (phone_number,))
    result = c.fetchone()
    
    if result:
        description, latitude, longitude = result
        
        # Insert complete report
        c.execute('''
        INSERT INTO reports (phone_number, description, latitude, longitude, image_path) 
        VALUES (?, ?, ?, ?, ?)
        ''', (phone_number, description, latitude, longitude, image_path))
        
        # Reset conversation (but keep the phone number entry)
        c.execute('''
        UPDATE conversations 
        SET state = ?, description = NULL, latitude = NULL, longitude = NULL 
        WHERE phone_number = ?
        ''', (COMPLETE, phone_number))
        
        conn.commit()
    
    conn.close()

# Main webhook route
@app.route('/pothole', methods=['POST'])
def webhook():
    # Get the incoming message details
    incoming_msg = request.values.get('Body', '').strip()
    phone_number = request.values.get('From', '')
    num_media = int(request.values.get('NumMedia', 0))
    
    # Initialize response
    resp = MessagingResponse()
    
    # Get current conversation state
    state = get_conversation_state(phone_number)
    
    # Check if this is a location message
    latitude = request.values.get('Latitude')
    longitude = request.values.get('Longitude')
    has_location = latitude is not None and longitude is not None
    print(f"Latitude: {latitude}, Longitude: {longitude}, Has Location: {has_location}")
    print(f"Incoming message: {incoming_msg}, Phone number: {phone_number}, State: {state}")
    
    # Handle conversation based on state
    if state == DESCRIBE_LOCATION:
        if incoming_msg.lower() in ['hi', 'hello', 'start']:
            resp.message("Welcome to the Pothole Reporter! Please describe where the pothole is located (e.g., street name, nearest intersection, landmark).")
        else:
            save_description(phone_number, incoming_msg)
            update_conversation_state(phone_number, SHARE_LOCATION)
            resp.message("Thank you! Now, please share your location by using WhatsApp's location sharing feature.")
    
    elif state == SHARE_LOCATION:
        if has_location:
            save_location(phone_number, float(latitude), float(longitude))
            update_conversation_state(phone_number, SHARE_PHOTO)
            resp.message("Got your location! Finally, please take and send a photo of the pothole.")
        else:
            resp.message("I need your location to continue. Please use WhatsApp's location sharing feature to share your current location.")
    
    elif state == SHARE_PHOTO:
        if num_media > 0:
            # Process and save the image
            media_url = request.values.get(f'MediaUrl0')

            # Make the authenticated request
            response = requests.get(media_url, headers=headers)

            # The response URL is a temporary link to the media file
            print(response.url)

            media_content_type = request.values.get(f'MediaContentType0')
            
            if 'image' in media_content_type:
                # Download the image
                image_data = requests.get(media_url).content
                filename = f"{phone_number.replace('whatsapp:', '')}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
                filepath = os.path.join(UPLOAD_FOLDER, secure_filename(filename))
                
                with open(filepath, 'wb') as f:
                    f.write(image_data)
                
                # Save the complete report
                save_report(phone_number, filepath)
                
                resp.message("Thank you for reporting the pothole! Your report has been received and saved.")
                
                # Reset conversation state
                update_conversation_state(phone_number, COMPLETE)
            else:
                resp.message("Please send an image file of the pothole.")
        else:
            resp.message("Please send a photo of the pothole to complete your report.")
    
    elif state == COMPLETE:
        # Start a new report
        update_conversation_state(phone_number, DESCRIBE_LOCATION)
        resp.message("Welcome back to the Pothole Reporter! Please describe where the pothole is located (e.g., street name, nearest intersection, landmark).")
    
    return str(resp)

if __name__ == '__main__':
    app.run(debug=True, port=8008)