from flask import Flask, request, jsonify, session
from flask_cors import CORS
import google.generativeai as genai
import json
import logging
import os
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from typing import Dict, Any
import uuid

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-change-in-production')
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)

CORS(app, resources={r"/*": {"origins": "*", "supports_credentials": True}})

# Logging setup
def setup_logging():
    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    log_file = os.path.join(log_dir, 'funds_app.log')
    handler = RotatingFileHandler(log_file, maxBytes=10000000, backupCount=5)
    handler.setFormatter(logging.Formatter(
        '[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
    ))
    
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    
    if app.debug:
        app.logger.addHandler(logging.StreamHandler())

setup_logging()

genai.configure(api_key="AIzaSyCD6DGeERwWQbBC6BK1Hq0ecagQj72rqyQ")

@dataclass
class UserSession:
    session_id: str
    conversation_history: str
    flow: str  # 'FUNDS_TRANSFER' or 'FUNDS_DEPOSIT'
    created_at: datetime
    last_updated: datetime

def get_gemini_model():
    return genai.GenerativeModel(
        "gemini-1.5-pro",
        safety_settings=[
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
        ]
    )

def determine_user_intent(model, user_input, conversation_history):
    system_prompt = """You are a banking assistant specializing in funds management.
    Your goal is to determine whether the user wants to transfer funds or deposit funds.
    
    - If the user mentions sending money, transferring money, IMPS, NEFT, RTGS, paying someone, or moving funds, classify as FUNDS_TRANSFER.
    - If the user mentions depositing money, adding funds, saving money, fixed deposits, FD, recusrring deposits, RD or any other classify as FUNDS_DEPOSIT.
    
    Return a JSON object:
    {
        "intent": "FUNDS_TRANSFER" or "FUNDS_DEPOSIT" or "UNKNOWN"
    }
    
    If unclear, respond with "UNKNOWN" and ask a clarifying question.
    """
    full_prompt = f"{system_prompt}\n\nConversation so far:\n{conversation_history}\n\nUser: {user_input}"
    
    response = model.generate_content(full_prompt).text.strip()
    
    try:
        app.logger.info(f"response: {response.text}")
        return json.loads(response)
    except json.JSONDecodeError:
        return {"intent": "UNKNOWN"}

@app.route('/api/start', methods=['POST'])
def start_conversation():
    try:
        session_id = str(uuid.uuid4())
        welcome_message = """Hello! I'm your banking assistant. How can I assist you today?
        I can help with Funds Transfer and Funds Deposit. Just let me know what you need."""
        
        user_session = UserSession(
            session_id=session_id,
            conversation_history=f"System: {welcome_message}\n",
            flow="UNKNOWN",
            created_at=datetime.utcnow(),
            last_updated=datetime.utcnow()
        )
        
        session['user_session'] = {
            'session_id': user_session.session_id,
            'conversation_history': user_session.conversation_history,
            'flow': user_session.flow,
            'created_at': user_session.created_at.isoformat(),
            'last_updated': user_session.last_updated.isoformat()
        }
        
        app.logger.info(f"Started new session: {session_id}")
        
        return jsonify({
            'message': 'Conversation started',
            'session_id': session_id,
            'next_prompt': welcome_message
        })
    
    except Exception as e:
        app.logger.error(f"Error starting conversation: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/process', methods=['POST'])
def process_conversation():
    try:
        data = request.get_json()
        user_input = data.get('message', '').strip()
        session_id = data.get('session_id')
        
        if not user_input:
            return jsonify({'error': 'Message is required'}), 400
        if not session_id:
            return jsonify({'error': 'Session ID is required'}), 400
        
        user_data = session.get('user_session')
        if not user_data or user_data['session_id'] != session_id:
            return jsonify({'error': 'No active session'}), 404
        
        user_data['conversation_history'] += f"User: {user_input}\n"
        model = get_gemini_model()
        intent_response = determine_user_intent(model, user_input, user_data['conversation_history'])
        
        intent = intent_response.get("intent", "UNKNOWN")
        user_data['flow'] = intent
        user_data['last_updated'] = datetime.utcnow().isoformat()
        
        next_prompt = """
        Thank you! I will guide you through the process.
        """ if intent in ["FUNDS_TRANSFER", "FUNDS_DEPOSIT"] else "Could you please clarify? Do you want to transfer or deposit funds?"
        
        if intent in ["FUNDS_TRANSFER", "FUNDS_DEPOSIT"]:
            session.pop('user_session', None)
        
        return jsonify({
            'intent': intent,
            'next_prompt': next_prompt
        })
    
    except Exception as e:
        app.logger.error(f"Error processing conversation: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5001)))
