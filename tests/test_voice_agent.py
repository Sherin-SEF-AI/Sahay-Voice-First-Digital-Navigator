"""Tests for the Voice Agent components."""

import pytest

from app.voice_agent.intent_parser import parse_intent, TaskIntent, _detect_language


class TestIntentParser:
    """Tests for intent parsing from voice transcriptions."""

    def test_hindi_pension_intent(self):
        """Hindi pension check should parse correctly."""
        intent = parse_intent("Mera pension ka status dekhna hai")
        assert intent.action == "check_status"
        assert intent.target_service == "epfo"

    def test_english_train_booking(self):
        """English train booking should parse correctly."""
        intent = parse_intent("Book a train to Chennai")
        assert intent.action == "book"
        assert intent.target_service == "irctc"
        assert intent.start_url == "https://www.irctc.co.in"

    def test_aadhaar_download(self):
        """Aadhaar download request should map to DigiLocker."""
        intent = parse_intent("Download my Aadhaar card")
        assert intent.action == "download"
        assert intent.target_service == "digilocker"

    def test_electricity_bill(self):
        """Electricity bill payment should parse correctly."""
        intent = parse_intent("Pay my electricity bill")
        assert intent.action == "pay_bill"
        assert intent.target_service == "utility"

    def test_passport_intent(self):
        """Passport booking should parse correctly."""
        intent = parse_intent("Book passport appointment")
        assert intent.action == "book_appointment"
        assert intent.target_service == "passport_seva"

    def test_unknown_intent_fallback(self):
        """Unknown intents should fall back to free-form."""
        intent = parse_intent("Show me the weather forecast")
        assert intent.action == "navigate"
        assert intent.target_service == "unknown"
        assert intent.raw_text == "Show me the weather forecast"
        assert intent.confidence < 0.5

    def test_city_extraction(self):
        """City names should be extracted as parameters."""
        intent = parse_intent("Book a train from Delhi to Mumbai")
        assert "from" in intent.parameters or "destination" in intent.parameters

    def test_hindi_script_detection(self):
        """Devanagari text should be detected as Hindi."""
        lang = _detect_language("मेरा पेंशन का स्टेटस देखना है")
        assert lang == "hi"

    def test_english_detection(self):
        """ASCII-only text should be detected as English."""
        lang = _detect_language("Check my pension status")
        assert lang == "en"

    def test_tamil_detection(self):
        """Tamil script should be detected."""
        lang = _detect_language("என் ஓய்வூதியத்தை சரிபார்க்கவும்")
        assert lang == "ta"

    def test_intent_has_raw_text(self):
        """Every intent should preserve raw input text."""
        text = "I want to check my bank balance"
        intent = parse_intent(text)
        assert intent.raw_text == text


class TestVoiceAgentCreation:
    """Tests for voice agent factory."""

    def test_create_voice_agent(self):
        """Voice agent should be created successfully."""
        from app.voice_agent.agent import create_voice_agent
        agent = create_voice_agent()
        assert agent.name == "sahay_voice_agent"

    def test_voice_agent_has_tools(self):
        """Voice agent should have function tools registered."""
        from app.voice_agent.agent import create_voice_agent
        agent = create_voice_agent()
        assert len(agent.tools) == 4
