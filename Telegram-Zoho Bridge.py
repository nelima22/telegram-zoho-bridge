"""
Telegram-Zoho Desk Bridge
Connects Telegram group messages to Zoho Desk tickets and vice versa
"""

from flask import Flask, request, jsonify
import requests
import os
from dotenv import load_dotenv
import logging

# Load environment variables from .env file
load_dotenv()

# Setup logging so we can see what's happening in the terminal
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create Flask app (this handles web requests)
app = Flask(__name__)

# Load configuration from .env file
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_GROUP_CHAT_ID = os.getenv('TELEGRAM_GROUP_CHAT_ID')
ZOHO_ORG_ID = os.getenv('ZOHO_ORG_ID')
ZOHO_DEPARTMENT_ID = os.getenv('ZOHO_DEPARTMENT_ID')
ZOHO_ACCESS_TOKEN = os.getenv('ZOHO_ACCESS_TOKEN')
ZOHO_REFRESH_TOKEN = os.getenv('ZOHO_REFRESH_TOKEN')
ZOHO_CLIENT_ID = os.getenv('ZOHO_CLIENT_ID')
ZOHO_CLIENT_SECRET = os.getenv('ZOHO_CLIENT_SECRET')
ZOHO_API_DOMAIN = os.getenv('ZOHO_API_DOMAIN', 'https://desk.zoho.com')

# Store current access token in memory
current_access_token = ZOHO_ACCESS_TOKEN

# Store which messages created which tickets (simple in-memory storage)
message_ticket_map = {}


def refresh_zoho_token():
    """
    Automatically refresh the Zoho access token using refresh token
    """
    global current_access_token
    
    try:
        if not ZOHO_REFRESH_TOKEN or not ZOHO_CLIENT_ID or not ZOHO_CLIENT_SECRET:
            logger.error("❌ Missing refresh token or client credentials")
            return False
        
        logger.info("🔄 Refreshing Zoho access token...")
        
        url = "https://accounts.zoho.com/oauth/v2/token"
        data = {
            'refresh_token': ZOHO_REFRESH_TOKEN,
            'client_id': ZOHO_CLIENT_ID,
            'client_secret': ZOHO_CLIENT_SECRET,
            'grant_type': 'refresh_token'
        }
        
        response = requests.post(url, data=data)
        
        if response.status_code == 200:
            result = response.json()
            current_access_token = result.get('access_token')
            logger.info("✅ Token refreshed successfully!")
            return True
        else:
            logger.error(f"❌ Failed to refresh token: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error refreshing token: {str(e)}")
        return False


def get_zoho_headers():
    """
    Get headers for Zoho API requests with current access token
    """
    return {
        'Authorization': f'Zoho-oauthtoken {current_access_token}',
        'orgId': ZOHO_ORG_ID,
        'Content-Type': 'application/json'
    }


# ========================================
# PART 1: TELEGRAM → ZOHO DESK
# When someone sends a message in Telegram,
# we create a ticket in Zoho Desk
# ========================================

@app.route('/telegram-webhook', methods=['POST'])
def telegram_webhook():
    """
    This function runs whenever Telegram sends us a message
    Telegram will POST to this URL whenever there's activity in your group
    """
    try:
        # Get the message data from Telegram
        update = request.json
        logger.info("📱 Received message from Telegram")
        
        # Check if it's a regular message (not a command, sticker, etc.)
        if 'message' in update:
            message = update['message']
            chat_id = str(message['chat']['id'])
            
            # Make sure it's from our specific group
            if chat_id == TELEGRAM_GROUP_CHAT_ID:
                handle_telegram_message(message)
            else:
                logger.info(f"⚠️ Message from different chat: {chat_id}")
        
        return jsonify({'ok': True})
    
    except Exception as e:
        logger.error(f"❌ Error processing Telegram message: {str(e)}")
        return jsonify({'ok': False}), 500


def handle_telegram_message(message):
    """
    Process a Telegram message and create a Zoho Desk ticket
    """
    try:
        # Extract information from the message
        text = message.get('text', '')
        
        # Skip if there's no text (could be an image, sticker, etc.)
        if not text:
            logger.info("⏭️ Skipping non-text message")
            return
        
        # Get information about who sent the message
        from_user = message['from']
        first_name = from_user.get('first_name', 'Unknown')
        last_name = from_user.get('last_name', '')
        username = from_user.get('username', '')
        
        logger.info(f"💬 Message from {first_name}: {text[:50]}...")
        
        # Step 1: Get or create a contact in Zoho for this Telegram user
        contact_id = get_or_create_contact(from_user)
        
        if not contact_id:
            logger.error("❌ Couldn't get contact ID")
            return
        
        # Step 2: Create the ticket in Zoho Desk
        ticket_data = {
            "subject": f"Telegram: {first_name} {last_name}".strip(),
            "description": text,
            "departmentId": ZOHO_DEPARTMENT_ID,
            "contactId": contact_id,
            "channel": "Chat",
            "status": "Open"
        }
        
        ticket = create_zoho_ticket(ticket_data)
        
        if ticket:
            ticket_number = ticket.get('ticketNumber', 'N/A')
            logger.info(f"✅ Created Zoho Desk ticket #{ticket_number}")
            
            # Send confirmation back to Telegram
            send_telegram_message(f"✅ Ticket #{ticket_number} created!")
        else:
            logger.error("❌ Failed to create ticket")
            send_telegram_message("❌ Sorry, couldn't create ticket. Check logs.")
        
    except Exception as e:
        logger.error(f"❌ Error handling message: {str(e)}")


def get_or_create_contact(telegram_user):
    """
    Check if we already have this Telegram user in Zoho Desk as a contact.
    If not, create them. Returns the contact ID.
    """
    try:
        # Use Telegram username or ID as a unique email identifier
        # Using example.com (a real domain reserved for documentation/examples)
        username = telegram_user.get('username', '')
        user_id = telegram_user['id']
        email = f"telegram_{username or user_id}@example.com"
        
        logger.info(f"🔍 Looking for contact with email: {email}")
        
        # Search for existing contact in Zoho
        search_url = f"{ZOHO_API_DOMAIN}/api/v1/contacts/search"
        headers = get_zoho_headers()
        params = {'email': email}
        
        response = requests.get(search_url, headers=headers, params=params)
        
        # If unauthorized, try refreshing token
        if response.status_code == 401:
            logger.warning("⚠️ Token expired, refreshing...")
            if refresh_zoho_token():
                headers = get_zoho_headers()
                response = requests.get(search_url, headers=headers, params=params)
        
        # If contact exists, return their ID
        if response.status_code == 200:
            data = response.json().get('data', [])
            if data:
                contact_id = data[0]['id']
                logger.info(f"✅ Found existing contact: {contact_id}")
                return contact_id
        
        # Contact doesn't exist, so create a new one
        logger.info("📝 Creating new contact...")
        contact_data = {
            "firstName": telegram_user.get('first_name', 'Telegram'),
            "lastName": telegram_user.get('last_name', 'User'),
            "email": email
        }
        
        create_url = f"{ZOHO_API_DOMAIN}/api/v1/contacts"
        response = requests.post(create_url, json=contact_data, headers=headers)
        
        # If unauthorized, try refreshing token
        if response.status_code == 401:
            logger.warning("⚠️ Token expired, refreshing...")
            if refresh_zoho_token():
                headers = get_zoho_headers()
                response = requests.post(create_url, json=contact_data, headers=headers)
        
        if response.status_code == 200:
            contact_id = response.json()['id']
            logger.info(f"✅ Created new contact: {contact_id}")
            return contact_id
        else:
            logger.error(f"❌ Failed to create contact: {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error with contact: {str(e)}")
        return None


def create_zoho_ticket(ticket_data):
    """
    Create a ticket in Zoho Desk with the given data
    """
    try:
        url = f"{ZOHO_API_DOMAIN}/api/v1/tickets"
        headers = get_zoho_headers()
        
        logger.info("📤 Sending ticket to Zoho Desk...")
        response = requests.post(url, json=ticket_data, headers=headers)
        
        # If unauthorized, try refreshing token
        if response.status_code == 401:
            logger.warning("⚠️ Token expired, refreshing...")
            if refresh_zoho_token():
                headers = get_zoho_headers()
                response = requests.post(url, json=ticket_data, headers=headers)
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"❌ Zoho API error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error creating ticket: {str(e)}")
        return None


# ========================================
# PART 2: ZOHO DESK → TELEGRAM
# When an agent replies in Zoho Desk,
# we send that reply back to Telegram
# ========================================

@app.route('/zoho-webhook', methods=['POST', 'GET'])
def zoho_webhook():
    """
    This function runs when Zoho Desk sends us updates
    (like when an agent replies to a ticket)
    """
    try:
        # Handle GET requests for validation
        if request.method == 'GET':
            logger.info("✅ Zoho webhook validation request")
            return jsonify({'status': 'ok', 'message': 'Webhook endpoint is active'})
        
        # Handle POST requests with actual data
        event = request.json
        logger.info("🎫 Received update from Zoho Desk")
        
        event_type = event.get('eventType', '')
        
        # Check if it's a reply or comment we should send to Telegram
        if 'REPLY' in event_type or 'COMMENT' in event_type:
            handle_zoho_reply(event)
        
        return jsonify({'ok': True})
    
    except Exception as e:
        logger.error(f"❌ Error processing Zoho webhook: {str(e)}")
        return jsonify({'ok': False}), 500


def handle_zoho_reply(event):
    """
    Process a reply from Zoho Desk and send it to Telegram
    """
    try:
        ticket_number = event.get('ticketNumber', 'N/A')
        content = event.get('content', '')
        author = event.get('author', 'Support Agent')
        
        logger.info(f"💬 Reply from {author} on ticket #{ticket_number}")
        
        # Format a nice message for Telegram
        message = (
            f"💬 <b>Reply on Ticket #{ticket_number}</b>\n"
            f"👤 {author}\n\n"
            f"{content}"
        )
        
        send_telegram_message(message)
        logger.info("✅ Sent reply to Telegram")
        
    except Exception as e:
        logger.error(f"❌ Error handling Zoho reply: {str(e)}")


def send_telegram_message(text):
    """
    Send a message to the Telegram group
    """
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            'chat_id': TELEGRAM_GROUP_CHAT_ID,
            'text': text,
            'parse_mode': 'HTML'
        }
        
        response = requests.post(url, json=data)
        
        if response.status_code != 200:
            logger.error(f"❌ Failed to send to Telegram: {response.text}")
            
    except Exception as e:
        logger.error(f"❌ Error sending to Telegram: {str(e)}")


# ========================================
# HELPER ENDPOINTS
# These help you set up and test the bridge
# ========================================

@app.route('/')
def home():
    """
    Simple homepage so you know the server is running
    """
    return """
    <h1>🌉 Telegram-Zoho Bridge is Running!</h1>
    <p>✅ Server is active</p>
    <ul>
        <li><a href="/health">Check Health</a></li>
        <li><a href="/webhook-info">Check Telegram Webhook</a></li>
        <li><a href="/setup-webhook">Setup Telegram Webhook</a></li>
    </ul>
    """


@app.route('/health')
def health_check():
    """
    Check if everything is configured correctly
    """
    config_ok = all([
        TELEGRAM_BOT_TOKEN,
        TELEGRAM_GROUP_CHAT_ID,
        ZOHO_ORG_ID,
        ZOHO_DEPARTMENT_ID,
        ZOHO_ACCESS_TOKEN
    ])
    
    return jsonify({
        'status': 'ok' if config_ok else 'missing configuration',
        'telegram_configured': bool(TELEGRAM_BOT_TOKEN and TELEGRAM_GROUP_CHAT_ID),
        'zoho_configured': bool(ZOHO_ORG_ID and ZOHO_DEPARTMENT_ID and ZOHO_ACCESS_TOKEN)
    })


@app.route('/setup-webhook')
def setup_webhook():
    """
    Tell Telegram where to send updates (your webhook URL)
    Visit this URL in your browser after deployment
    """
    try:
        webhook_url = os.getenv('WEBHOOK_URL')
        if not webhook_url:
            return jsonify({
                'error': 'WEBHOOK_URL not set in .env file',
                'help': 'Add WEBHOOK_URL=https://your-domain.com to your .env'
            }), 400
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
        data = {'url': f"{webhook_url}/telegram-webhook"}
        
        response = requests.post(url, json=data)
        result = response.json()
        
        return jsonify({
            'success': result.get('ok', False),
            'description': result.get('description', 'Unknown'),
            'webhook_url': f"{webhook_url}/telegram-webhook"
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/webhook-info')
def webhook_info():
    """
    Check what webhook Telegram currently has registered
    """
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo"
        response = requests.get(url)
        return jsonify(response.json())
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# START THE SERVER
# ========================================

if __name__ == '__main__':
    print("\n" + "="*50)
    print("🚀 Starting Telegram-Zoho Bridge")
    print("="*50)
    
    # Check if configuration is loaded
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_GROUP_CHAT_ID, ZOHO_ORG_ID, 
                ZOHO_DEPARTMENT_ID, ZOHO_ACCESS_TOKEN]):
        print("⚠️  WARNING: Missing configuration in .env file!")
        print("Please check that all variables are set correctly.")
    else:
        print("✅ Configuration loaded")
        print(f"📱 Telegram Bot: {TELEGRAM_BOT_TOKEN[:10]}...")
        print(f"💼 Zoho Org: {ZOHO_ORG_ID}")
    
    print("="*50 + "\n")
    
    # Start the Flask web server
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
