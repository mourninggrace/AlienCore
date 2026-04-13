"""
AlienCore Backend - mail.py
Sends transactional emails via SMTP (Gmail recommended).
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from backend.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, FROM_EMAIL, FROM_NAME, KYLE_EMAIL


def _send(to: str, subject: str, body_text: str, body_html: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"]      = to
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.ehlo()
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(FROM_EMAIL, to, msg.as_string())


def send_pin_email(to_email: str, pin: str):
    subject = "Your AlienCore Login Code"

    text = f"""Your AlienCore login code is:

  {pin}

This code expires in 10 minutes. Do not share it with anyone.
If you didn't request this, you can safely ignore this email.

— Kyle Yeroshefsky / AlienCore"""

    html = f"""<html>
<body style="margin:0;padding:0;background:#111111;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#111111">
  <tr><td align="center" style="padding:40px 20px;">
    <table width="480" cellpadding="0" cellspacing="0"
           style="background:#1a1a1a;border-radius:12px;overflow:hidden;">
      <tr>
        <td style="background:#0d1a2a;padding:24px 32px;">
          <span style="font-size:24px;font-weight:bold;color:#00aaff;">AlienCore</span>
          <span style="font-size:12px;color:#555;margin-left:10px;">System Optimizer</span>
        </td>
      </tr>
      <tr>
        <td style="padding:32px;">
          <p style="color:#aaa;font-size:15px;margin:0 0 20px 0;">
            Your one-time login code is:
          </p>
          <div style="background:#0a0a0a;border:1px solid #00aaff;border-radius:8px;
                      padding:20px 0;text-align:center;margin-bottom:24px;">
            <span style="font-size:42px;font-weight:bold;letter-spacing:14px;
                         color:#00aaff;font-family:Consolas,monospace;">{pin}</span>
          </div>
          <p style="color:#666;font-size:12px;margin:0 0 8px 0;">
            This code expires in <strong style="color:#aaa;">10 minutes</strong>.
            Do not share it with anyone.
          </p>
          <p style="color:#444;font-size:11px;margin:0;">
            If you didn't request this, ignore this email — your account is safe.
          </p>
        </td>
      </tr>
      <tr>
        <td style="background:#0d0d0d;padding:16px 32px;
                   border-top:1px solid #222;text-align:center;">
          <span style="color:#333;font-size:11px;">
            AlienCore by Kyle Yeroshefsky &nbsp;·&nbsp; mourning.grace.2014@gmail.com
          </span>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""

    _send(to_email, subject, text, html)


def send_support_notification(from_email: str, message: str):
    """Notify Kyle that a priority support ticket was submitted."""
    subject = f"[AlienCore Support] New ticket from {from_email}"

    text = f"""Priority support ticket received.

From: {from_email}

Message:
{message}

---
Reply directly to {from_email} within 24 hours per the support agreement.
If this issue cannot be resolved, issue a PayPal refund and run:
  POST /paypal/refund-support  {{admin_key, email}}
"""
    html = f"""<html><body style="font-family:monospace;background:#111;color:#eee;padding:24px;">
<h3 style="color:#00aaff;">AlienCore Priority Support Ticket</h3>
<p><strong>From:</strong> {from_email}</p>
<hr style="border-color:#333;">
<pre style="background:#1a1a1a;padding:16px;border-radius:6px;">{message}</pre>
<hr style="border-color:#333;">
<p style="color:#888;font-size:12px;">
Reply to {from_email} within 24 hours.<br>
If unresolvable: issue PayPal refund + call
<code>POST /paypal/refund-support</code> to restore their credit.
</p>
</body></html>"""

    _send(KYLE_EMAIL, subject, text, html)
