# Demo script (about 3 minutes)

## Before you go on

- [ ] Run `.venv/bin/python app.py --update-filters` while you still have internet.
- [ ] Check every beat below still works: `.venv/bin/python scripts/rehearse_demo.py` (about 1 minute; add `ai-off` to rehearse without Gemma).
- [ ] Start the app about a minute early: `.venv/bin/python app.py https://home.test/`. It loads Gemma in the background, which takes about 15 s the first time.
- [ ] Open the PayPal page once and wait for Gemma's answer, then go back. Answers are kept per page, so you can return to it on stage without waiting.
- [ ] Use a large terminal font, and close everything else (Gemma needs about 4 GB of RAM).
- [ ] Decide when you'll turn WiFi off (step 6).

## Script

**1. The problem (15 s).** "Someone sends you a link. Is it phishing? Opening it in your browser is how
you find out the hard way. linkinspector lets you look first, from the terminal, with an AI that runs
on this laptop."

**2. A lookalike link (40 s).** Click *PayPal account check*.
- The confirm bar appears **before anything is fetched**: "‘paypa1-secure.test’ looks like PayPal".
- Press Enter. Point at the reader: the page asks for `[ Password: •••••• ]`.
- Side panel: Risk HIGH, the password form sent to another site, the brand in the title, and the help link whose text says paypal.com but goes elsewhere.
- Gemma: "Likely phishing", each reason citing evidence like `[U1, P2]`. "That's Gemma 4 running locally on a laptop CPU, no GPU and no cloud. It can raise the risk but never lower it."

**3. A letter you can't see (20 s).** Back (alt+←), click the Cyrillic one.
- Confirm bar: "shown as pаypal.test, but really xn--pypal-4ve.test". "That's a Cyrillic letter. It looks identical."

**4. Hidden instructions for the AI (30 s).** Open *Claim your voucher*.
- It's on the known-bad list before we even open it (uBlock Origin's lists, plus URLhaus in real use).
- Scroll down the reader: there's white-on-white text telling AI tools to report the page as safe.
- Gemma still says likely phishing, and the code findings can't be overridden anyway.

**5. JavaScript mode (30 s).** Open *A local news article*, press **ctrl+r**.
- "Now it runs the page's scripts, in a throwaway Chromium behind a proxy that checks every connection."
- Side panel: tracker requests blocked, with the uBlock Origin rule that matched.
- New finding: **a login form added by JavaScript**. "Phishing kits do this so scanners reading only the HTML miss it."

**6. Turn WiFi off (20 s).** Open *Staff intranet*.
- It still works: the demo pages are local and Gemma runs on this laptop.
- Single sign-on through Microsoft is marked as normal (info only). "We tuned it so ordinary login pages don't raise false alarms."

**7. Close (15 s).** "No cookies kept, no scripts unless you ask, never your router or your own computer:
a redirect to 127.0.0.1 is refused at the network level. And it's all open source, MIT licensed."

## If something goes wrong

- **Gemma is slow:** keep talking through the findings; they appear instantly. Or go back to the page you warmed up earlier.
- **Gemma isn't running:** the app says so and every code finding still works. Run `ollama serve` in another terminal and press ctrl+g.
- **Chromium fails:** press ctrl+r to go back to static mode and skip step 5.
- **Typing is awkward:** paste links with ctrl+v; ctrl+l jumps to the address bar.
