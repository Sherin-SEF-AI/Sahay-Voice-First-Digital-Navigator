"""UPI Payment Service — Deep link and QR code generation.

Detects payment amounts on web pages, generates UPI deep links
and QR codes that users can scan with Google Pay, PhonePe, or Paytm.
Monitors the page for payment confirmation.
"""

import base64
import io
import logging
import re
from dataclasses import dataclass
from typing import Optional

import qrcode
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Common UPI apps for deep links
UPI_APPS = {
    "gpay": {"name": "Google Pay", "package": "com.google.android.apps.nbu.paisa.user"},
    "phonepe": {"name": "PhonePe", "package": "com.phonepe.app"},
    "paytm": {"name": "Paytm", "package": "net.one97.paytm"},
}

# Patterns to detect payment amounts on Indian portals
AMOUNT_PATTERNS = [
    r"(?:total|amount|payable|pay)\s*[:\-]?\s*(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d{1,2})?)",
    r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d{1,2})?)\s*(?:only|total|payable|due)",
    r"(?:total|grand total|net amount|amount payable)\s*[:\-]?\s*([\d,]+(?:\.\d{1,2})?)",
]

# Patterns to detect UPI IDs on payment pages
UPI_ID_PATTERNS = [
    r"([a-zA-Z0-9._-]+@[a-zA-Z0-9]+)",  # standard UPI ID format
]

# Patterns that indicate a payment page
PAYMENT_PAGE_INDICATORS = [
    r"payment\s+gateway",
    r"complete\s+payment",
    r"pay\s+now",
    r"proceed\s+to\s+pay",
    r"payment\s+method",
    r"select\s+payment",
    r"upi\s+payment",
    r"net\s+banking",
    r"debit\s+card",
    r"credit\s+card",
]

# Payment confirmation patterns
PAYMENT_SUCCESS_PATTERNS = [
    r"payment\s+successful",
    r"transaction\s+successful",
    r"payment\s+received",
    r"payment\s+confirmed",
    r"thank\s+you\s+for\s+(?:your\s+)?payment",
    r"transaction\s+id\s*[:\-]?\s*([A-Z0-9]+)",
    r"reference\s+(?:no|number|id)\s*[:\-]?\s*([A-Z0-9]+)",
]


@dataclass
class PaymentInfo:
    """Detected payment information from a web page."""
    amount: float
    currency: str = "INR"
    merchant_name: str = ""
    merchant_upi_id: str = ""
    description: str = ""
    is_payment_page: bool = False
    transaction_ref: str = ""


@dataclass
class UPIPayment:
    """A UPI payment ready for user action."""
    upi_deep_link: str
    qr_code_base64: str
    amount: float
    merchant_name: str
    merchant_upi_id: str
    description: str


class UPIService:
    """Generates UPI deep links and QR codes for payments."""

    def detect_payment_page(self, page_text: str) -> bool:
        """Check if the current page is a payment page."""
        text_lower = page_text.lower()
        matches = sum(
            1 for pattern in PAYMENT_PAGE_INDICATORS
            if re.search(pattern, text_lower)
        )
        return matches >= 2

    def extract_payment_info(
        self, page_text: str, url: str = ""
    ) -> Optional[PaymentInfo]:
        """Extract payment details from page text."""
        text_lower = page_text.lower()

        # Check if this is a payment page
        is_payment = self.detect_payment_page(page_text)

        # Extract amount
        amount = None
        for pattern in AMOUNT_PATTERNS:
            match = re.search(pattern, page_text, re.IGNORECASE)
            if match:
                amount_str = match.group(1).replace(",", "")
                try:
                    amount = float(amount_str)
                    break
                except ValueError:
                    continue

        if amount is None:
            return None

        # Extract UPI ID if visible
        upi_id = ""
        for pattern in UPI_ID_PATTERNS:
            match = re.search(pattern, page_text)
            if match:
                candidate = match.group(1)
                if "@" in candidate and not candidate.endswith((".com", ".in", ".org")):
                    upi_id = candidate
                    break

        # Extract merchant name from page/URL
        merchant_name = self._extract_merchant_name(page_text, url)

        return PaymentInfo(
            amount=amount,
            merchant_name=merchant_name,
            merchant_upi_id=upi_id,
            description=f"Payment of Rs.{amount:.2f}",
            is_payment_page=is_payment,
        )

    def generate_upi_payment(
        self,
        amount: float,
        merchant_upi_id: str = "",
        merchant_name: str = "Merchant",
        description: str = "",
        transaction_ref: str = "",
    ) -> UPIPayment:
        """Generate UPI deep link and QR code."""
        # Build UPI deep link
        upi_params = {
            "pa": merchant_upi_id or "merchant@upi",
            "pn": merchant_name,
            "am": f"{amount:.2f}",
            "cu": "INR",
        }
        if description:
            upi_params["tn"] = description[:50]
        if transaction_ref:
            upi_params["tr"] = transaction_ref

        param_str = "&".join(f"{k}={v}" for k, v in upi_params.items())
        deep_link = f"upi://pay?{param_str}"

        # Generate QR code
        qr_base64 = self._generate_qr_code(deep_link, amount, merchant_name)

        return UPIPayment(
            upi_deep_link=deep_link,
            qr_code_base64=qr_base64,
            amount=amount,
            merchant_name=merchant_name,
            merchant_upi_id=merchant_upi_id or "merchant@upi",
            description=description,
        )

    def detect_payment_success(self, page_text: str) -> Optional[dict]:
        """Check if the page shows payment confirmation."""
        text_lower = page_text.lower()

        for pattern in PAYMENT_SUCCESS_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                result = {"confirmed": True, "pattern": pattern}
                if match.groups():
                    result["transaction_id"] = match.group(1)
                return result

        return None

    def _generate_qr_code(
        self, data: str, amount: float, merchant_name: str
    ) -> str:
        """Generate a styled QR code as base64 PNG."""
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=8,
            border=2,
        )
        qr.add_data(data)
        qr.make(fit=True)

        # Create QR image
        qr_img = qr.make_image(fill_color="#1a1a2e", back_color="white").convert("RGB")

        # Add branding around QR
        qr_w, qr_h = qr_img.size
        canvas_w = qr_w + 40
        canvas_h = qr_h + 100

        canvas = Image.new("RGB", (canvas_w, canvas_h), "white")
        # Paste QR
        canvas.paste(qr_img, (20, 20))

        # Draw amount text below
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
            small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        except Exception:
            font = ImageFont.load_default()
            small_font = font

        amount_text = f"Pay Rs.{amount:,.2f}"
        # Center text
        bbox = draw.textbbox((0, 0), amount_text, font=font)
        text_w = bbox[2] - bbox[0]
        x = (canvas_w - text_w) // 2
        draw.text((x, qr_h + 28), amount_text, fill="#E8552D", font=font)

        merchant_text = f"to {merchant_name}"
        bbox2 = draw.textbbox((0, 0), merchant_text, font=small_font)
        text_w2 = bbox2[2] - bbox2[0]
        x2 = (canvas_w - text_w2) // 2
        draw.text((x2, qr_h + 52), merchant_text, fill="#666666", font=small_font)

        scan_text = "Scan with any UPI app"
        bbox3 = draw.textbbox((0, 0), scan_text, font=small_font)
        text_w3 = bbox3[2] - bbox3[0]
        x3 = (canvas_w - text_w3) // 2
        draw.text((x3, qr_h + 74), scan_text, fill="#999999", font=small_font)

        # Convert to base64
        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode()

    @staticmethod
    def _extract_merchant_name(page_text: str, url: str) -> str:
        """Try to extract merchant/service name from page content or URL."""
        # Common Indian portal names
        known_merchants = {
            "irctc": "IRCTC Railway",
            "sbi": "State Bank of India",
            "bsnl": "BSNL",
            "jio": "Jio",
            "airtel": "Airtel",
            "bescom": "BESCOM",
            "electricity": "Electricity Board",
            "water": "Water Board",
            "gas": "Gas Agency",
            "umang": "UMANG Portal",
            "passport": "Passport Seva",
        }

        text_lower = (page_text + " " + url).lower()
        for keyword, name in known_merchants.items():
            if keyword in text_lower:
                return name

        # Try to extract from page title pattern
        title_match = re.search(r"(?:payment|pay)\s+(?:to|for)\s+(.+?)(?:\s*[-|]|$)", page_text, re.IGNORECASE)
        if title_match:
            return title_match.group(1).strip()[:40]

        return "Online Payment"
