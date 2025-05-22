from base64 import b64encode
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from dotenv import load_dotenv
import os
from datetime import datetime
import requests
from werkzeug.utils import secure_filename
from flask_pymongo import PyMongo
from bson.objectid import ObjectId

load_dotenv()

app = Flask(__name__)

# Configure MongoDB
app.config["MONGO_URI"] = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/pothole_reports')
mongo = PyMongo(app)

# Configure Twilio client
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', 'your_account_sid')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', 'your_auth_token')
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Prepare the Basic Authentication header
auth_str = f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}"
auth_bytes = auth_str.encode('utf-8')
auth_b64 = b64encode(auth_bytes).decode('utf-8')
headers = {'Authorization': f'Basic {auth_b64}'}

# Configure upload folder for pothole images
UPLOAD_FOLDER = 'static/pothole_images'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Conversation states
DESCRIBE_LOCATION = "describe_location"
SHARE_LOCATION = "share_location"
SHARE_PHOTO = "share_photo"
COMPLETE = "complete"

# Helper function to get or create conversation state
def get_conversation_state(phone_number):
    conversation = mongo.db.conversations.find_one({"phone_number": phone_number})
    
    if conversation is None:
        # Initialize new conversation
        mongo.db.conversations.insert_one({
            "phone_number": phone_number,
            "state": DESCRIBE_LOCATION,
            "created_at": datetime.now()
        })
        return DESCRIBE_LOCATION
    else:
        return conversation.get("state", DESCRIBE_LOCATION)

# Update conversation state
def update_conversation_state(phone_number, state):
    mongo.db.conversations.update_one(
        {"phone_number": phone_number},
        {"$set": {"state": state}}
    )

# Save description to conversation
def save_description(phone_number, description):
    mongo.db.conversations.update_one(
        {"phone_number": phone_number},
        {"$set": {"description": description}}
    )

# Save location to conversation
def save_location(phone_number, latitude, longitude):
    mongo.db.conversations.update_one(
        {"phone_number": phone_number},
        {"$set": {
            "location": {
                "type": "Point",
                "coordinates": [longitude, latitude]  # GeoJSON format: [lng, lat]
            }
        }}
    )

# Save final report to database
def save_report(phone_number, image_path):
    # Get conversation data
    conversation = mongo.db.conversations.find_one({"phone_number": phone_number})
    
    if conversation:
        # Create a new report
        report = {
            "phone_number": phone_number,
            "description": conversation.get("description"),
            "location": conversation.get("location"),
            "image_path": image_path,
            "created_at": datetime.now()
        }
        
        # Insert the report
        mongo.db.reports.insert_one(report)
        
        # Reset conversation (but keep the phone number entry)
        mongo.db.conversations.update_one(
            {"phone_number": phone_number},
            {
                "$set": {"state": COMPLETE},
                "$unset": {"description": "", "location": ""}
            }
        )

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
            media_content_type = request.values.get('MediaContentType0')
            
            if 'image' in media_content_type:
                try:
                    
                    # Now we can get the authenticated URL
                    media_url = request.values.get(f'MediaUrl0')
                    
                    # Download the media with authentication
                    response = requests.get(
                        media_url,
                        headers=headers,
                        stream=True
                    )
                    
                    if response.status_code == 200:
                        # Save the image
                        filename = f"{phone_number.replace('whatsapp:', '')}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
                        filepath = os.path.join(UPLOAD_FOLDER, secure_filename(filename))
                        
                        with open(filepath, 'wb') as f:
                            f.write(response.content)
                        
                        # Save the complete report
                        save_report(phone_number, filepath)
                        
                        resp.message("Thank you for reporting the pothole! Your report has been received and saved.")
                        
                        # Reset conversation state
                        update_conversation_state(phone_number, COMPLETE)
                    else:
                        resp.message("Sorry, we had trouble downloading your image. Please try again.")
                except Exception as e:
                    print(f"Error processing media: {str(e)}")
                    resp.message("Sorry, we encountered an error processing your image. Please try again.")
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