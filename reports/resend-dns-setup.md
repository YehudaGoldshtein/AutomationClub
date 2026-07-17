# Resend DNS records for maxbaby.co.il

Four DNS records to add at Galcomm's DNS panel. Together they verify ownership, authorize Resend to send email using `@maxbaby.co.il` addresses, and route bounce feedback back to Resend.

## Records

### 1. DKIM (required)
- **Type:** TXT
- **Name / Host:** `resend._domainkey`
- **Value:** `p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC1PCyejU/4eFDJWYxCv1Rw18nJ0qZAKUSAWVOkqaV61YLV6b55vvRnALuO5Gfeyg6PRLDGmtXJA3AiMWNunJal4G6xR3z2vCIhyeWyimCofwV5SVsCW1sZVp0nIFnY9tGFD7h8pxiNbYsqQUUaidnr8l95CcZrG65oVGey0j4N5QIDAQAB`
- **TTL:** Auto / default (3600 is fine)

### 2. SPF (required)
- **Type:** TXT
- **Name / Host:** `send`
- **Value:** `v=spf1 include:amazonses.com ~all`
- **TTL:** Auto / default

### 3. MX on the send subdomain (required — for bounce feedback)
- **Type:** MX
- **Name / Host:** `send`
- **Value (target / exchange):** `feedback-smtp.eu-west-1.amazonses.com`
- **Priority:** `10` (standard if Resend doesn't specify)
- **TTL:** Auto / default

### 4. DMARC (optional, recommended)
- **Type:** TXT
- **Name / Host:** `_dmarc`
- **Value:** `v=DMARC1; p=none;`
- **TTL:** Auto / default

## Notes on subdomain SPF

Record #2 uses `send` as the host, not `@`. That means it applies to `send.maxbaby.co.il` — a subdomain Resend uses for the MAIL FROM / bounce path. Emails still appear to come from `@maxbaby.co.il`, and SPF alignment works correctly because Resend's return-path domain is `send.maxbaby.co.il`. This is the modern Resend pattern (avoids clashes with any future apex SPF).

## What these DO NOT change

- A record at `@maxbaby.co.il` (Shopify IP 23.227.38.65) — untouched, store keeps working
- Any MX records — there are none currently, we add none here. So `@maxbaby.co.il` does not receive email (separate question, not needed for this setup)
- TikTok verification TXT — untouched

## After adding

1. DNS propagation: 5–30 min typically. Sometimes up to 24h on slower providers.
2. Back in Resend (`resend.com/domains/maxbaby.co.il`), click **Verify DNS records**.
3. When verified (green checkmarks on all three), update `.env`:
   ```
   EMAIL_FROM=noreply@maxbaby.co.il
   NOTIFY_SYNC_SUMMARY_VIA=both   # (or wherever you want email routing)
   ```
4. First live email send: Eli receives at `Elishosh687@gmail.com` from `noreply@maxbaby.co.il`.
