# app/services/twilio_service.py

from twilio.rest import Client
from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM


def send_whatsapp(to: str, body: str):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=to, body=body)
