"""
OpenClaw TeamLab — Self-Evolution Logic
Generates and sends research trend digest emails to PI recipients.
"""
import hashlib
import logging
import smtplib
from collections import defaultdict
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select, and_

from config.settings import settings
from config.database import get_db
from models import ResearchTrend, EmailDigest, PiConfig

logger = logging.getLogger("teamlab.scheduler.evolution")


def _build_content_hash(trends_by_domain: dict) -> str:
    """Create a deterministic hash of trend content for deduplication."""
    parts = []
    for domain in sorted(trends_by_domain.keys()):
        for trend in trends_by_domain[domain]:
            parts.append(f"{trend.id}:{trend.trend_title}")
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _render_digest_html(trends_by_domain: dict) -> str:
    """Render the digest email using the Jinja2 template."""
    template_dir = settings.WEB_DIR / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=True,
    )
    template = env.get_template("email_digest.html")

    # Build template context
    domains = []
    for domain_name, trends in sorted(trends_by_domain.items()):
        items = []
        for t in trends:
            items.append({
                "title": t.trend_title or "Untitled Trend",
                "summary": t.summary or "",
                "relevance_score": float(t.relevance_score) if t.relevance_score else 0.0,
                "matched_students": t.matched_students or [],
                "source_urls": t.source_urls or [],
            })
        domains.append({
            "name": domain_name,
            "trends": items,
        })

    return template.render(
        domains=domains,
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        instance_name=settings.INSTANCE_NAME,
    )


async def _get_pi_recipients() -> list[str]:
    """Load PI email recipients from claw_pi_config table."""
    async with get_db() as db:
        result = (await db.execute(
            select(PiConfig).where(PiConfig.config_key == "digest_recipients")
        )).scalar_one_or_none()

        if result and result.config_value:
            value = result.config_value
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                return [e.strip() for e in value.split(",") if e.strip()]
        return []


async def _is_duplicate_digest(content_hash: str) -> bool:
    """Check if a digest with this content hash was sent recently (7 days)."""
    cutoff = datetime.utcnow() - timedelta(days=7)
    async with get_db() as db:
        result = (await db.execute(
            select(EmailDigest).where(
                and_(
                    EmailDigest.content_hash == content_hash,
                    EmailDigest.sent_at >= cutoff,
                )
            )
        )).scalar_one_or_none()
        return result is not None


def _send_email(recipient: str, subject: str, html_body: str):
    """Send an HTML email via SMTP."""
    msg = MIMEMultipart("alternative")
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = recipient
    msg["Subject"] = subject

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if settings.SMTP_USE_TLS:
            server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT)
        else:
            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
            server.starttls()

        if settings.SMTP_USER and settings.SMTP_AUTH_CODE:
            server.login(settings.SMTP_USER, settings.SMTP_AUTH_CODE)

        server.send_message(msg)
        server.quit()
        logger.info("Email sent to %s: %s", recipient, subject)
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", recipient, exc)
        raise


async def check_and_send_digest():
    """
    Main evolution entry point:
    1. Query claw_research_trends for un-notified items
    2. If none, skip
    3. Group by domain
    4. Load PI recipients from claw_pi_config
    5. Generate HTML email using Jinja2 template
    6. Check content_hash against recent digests (dedup)
    7. If new content, send via SMTP and log to claw_email_digests
    8. Mark trends as notified
    """
    # Step 1: Query un-notified trends
    async with get_db() as db:
        result = (await db.execute(
            select(ResearchTrend).where(ResearchTrend.notified == False)  # noqa: E712
            .order_by(ResearchTrend.discovered_at.desc())
        )).scalars().all()

        if not result:
            logger.info("No un-notified research trends — skipping digest")
            return

        # Step 3: Group by domain
        trends_by_domain: dict[str, list] = defaultdict(list)
        for trend in result:
            domain = trend.domain or "General"
            trends_by_domain[domain].append(trend)

        logger.info(
            "Found %d un-notified trends across %d domains",
            len(result),
            len(trends_by_domain),
        )

        # Step 4: Load recipients
        recipients = await _get_pi_recipients()
        if not recipients:
            logger.warning("No digest recipients configured in claw_pi_config — skipping send")
            return

        # Step 5: Generate HTML
        html_body = _render_digest_html(trends_by_domain)

        # Step 6: Deduplication
        content_hash = _build_content_hash(trends_by_domain)
        if await _is_duplicate_digest(content_hash):
            logger.info("Digest content unchanged (hash=%s…) — skipping", content_hash[:12])
            return

        # Step 7: Send emails and log
        subject = f"[TeamLab] Research Trend Digest — {datetime.utcnow().strftime('%Y-%m-%d')}"
        trend_ids = [t.id for t in result]

        for recipient in recipients:
            try:
                _send_email(recipient, subject, html_body)

                # Log to claw_email_digests table
                async with get_db() as log_db:
                    log_db.add(EmailDigest(
                        recipient_email=recipient,
                        digest_type="daily",
                        subject_line=subject,
                        content_hash=content_hash,
                        trend_ids=trend_ids,
                    ))
            except Exception as exc:
                logger.error("Digest send failed for %s: %s", recipient, exc)

        # Step 8: Mark trends as notified
        for trend in result:
            trend.notified = True
        await db.flush()

        logger.info(
            "Digest sent to %d recipients (%d trends)",
            len(recipients),
            len(result),
        )
