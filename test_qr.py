import pyotp
import qrcode
import qrcode.image.svg
from io import BytesIO

secret = pyotp.random_base32()
totp = pyotp.TOTP(secret)
uri = totp.provisioning_uri(name="test@test.com", issuer_name="Test")
img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgImage)
stream = BytesIO()
img.save(stream)
svg = stream.getvalue().decode()
print("SVG starts with:", svg[:50])
