"""Labelled demo data: customer-support tickets. Each entry is (expected option, ticket text).
The expected label is the author's best judgement and travels with the event so the dashboard can show right and wrong.
CLEAR tickets have one obvious home. AMBIGUOUS ones mix intents or sit on a boundary, so the label is debatable."""

BILLING = "billing or payment problem"
TECHNICAL = "technical fault or how-to question"
SALES = "sales or pricing enquiry"
ACCOUNT = "account access or security issue"
FEEDBACK = "general feedback or complaint"

CLEAR = [
    # --- billing
    (BILLING, "I was charged twice for my March subscription. Please refund the duplicate $29.99 charge."),
    (BILLING, "My invoice for April shows a plan I never signed up for. Can you correct it and send a new invoice?"),
    (BILLING, "The card on file expired and my payment keeps failing. How do I update my payment method?"),
    (BILLING, "I cancelled last month but I was billed again today. I want a refund."),
    (BILLING, "Where can I download receipts for the last 12 months? My accountant needs them for tax filing."),
    (BILLING, "Your system added sales tax to my order even though we are a tax-exempt nonprofit. Please fix the invoice."),
    (BILLING, "I upgraded mid-cycle and was charged the full annual price instead of a prorated amount."),
    (BILLING, "My PayPal payment went through but my account still shows as unpaid."),
    (BILLING, "Please change the billing address on my invoices to our new office address in Austin."),
    (BILLING, "I see an unfamiliar $14 charge from your company on my bank statement. What is it for?"),
    (BILLING, "Our finance team needs a purchase order number printed on every invoice going forward."),
    (BILLING, "The coupon code SPRING20 was not applied at checkout and I paid the full amount."),
    (BILLING, "A refund for order 48213 was promised two weeks ago but I have not received it yet."),
    (BILLING, "My annual renewal charged my old credit card even though I replaced it in January."),
    # --- technical
    (TECHNICAL, "The app crashes every time I try to export a report to PDF on Windows 11."),
    (TECHNICAL, "How do I connect your API to Zapier? The documentation link returns a 404."),
    (TECHNICAL, "Dashboard charts show no data since this morning, even though my data source is connected."),
    (TECHNICAL, "The mobile app keeps logging me out after a few seconds on Android 14."),
    (TECHNICAL, "I get a 504 timeout error when uploading files larger than 50 MB."),
    (TECHNICAL, "How can I set up single sign-on with Okta? I cannot find the SAML settings page."),
    (TECHNICAL, "Notifications are delayed by about 30 minutes. They used to arrive instantly."),
    (TECHNICAL, "The CSV import fails with 'Invalid column mapping' even though my headers match the template."),
    (TECHNICAL, "Search is returning results from projects I deleted. Is this a bug?"),
    (TECHNICAL, "Your webhook sends the same event three times. How do we make our handler idempotent?"),
    (TECHNICAL, "In dark mode the text on the settings page is white on light grey and unreadable."),
    (TECHNICAL, "Is there a way to schedule reports to be emailed every Monday? I could not find the option."),
    (TECHNICAL, "After the latest update the desktop client will not start; it shows a blank white window."),
    (TECHNICAL, "I get 429 errors at 60 requests per minute, but the API docs say the limit is 120."),
    # --- sales
    (SALES, "We are a team of 200 and would like a quote for an enterprise plan with SSO and a dedicated support manager."),
    (SALES, "What is the difference between the Pro and Business plans? Is there a discount for nonprofits?"),
    (SALES, "Do you offer a free trial of the Business tier? We would like to evaluate it for 30 days."),
    (SALES, "Can I schedule a demo for our sales leadership team next week?"),
    (SALES, "We are considering switching from a competitor. Do you offer migration help and volume pricing?"),
    (SALES, "Is there a reseller or partner programme? We are an IT consultancy with 40 clients."),
    (SALES, "What would it cost to add 25 more seats to our current plan?"),
    (SALES, "Do you have an education discount for universities? We would need about 500 licences."),
    (SALES, "Please send me your enterprise pricing sheet and security whitepaper for our procurement team."),
    (SALES, "Can we get a custom contract with a three-year price lock?"),
    (SALES, "I am interested in the on-premise deployment option. Who can I talk to about licensing?"),
    (SALES, "Are there any startup discounts? We are a five-person company that just launched."),
    (SALES, "Our fiscal year ends soon and we want to buy 100 licences before then. Can someone call me?"),
    (SALES, "Do you sell annual plans with invoicing, and what is the price for 15 users?"),
    # --- account
    (ACCOUNT, "I cannot log in. It says my password is incorrect, and the reset email never arrives."),
    (ACCOUNT, "I think someone else accessed my account. I got a login alert from a country I have never been to."),
    (ACCOUNT, "Please enable two-factor authentication for all users in our organisation."),
    (ACCOUNT, "I lost my phone and cannot get my authenticator codes. How can I regain access?"),
    (ACCOUNT, "I need to change the email address on my account because I left my old company."),
    (ACCOUNT, "Please delete my account and all my personal data under GDPR."),
    (ACCOUNT, "My account was locked after too many failed attempts. Can you unlock it?"),
    (ACCOUNT, "How do I transfer ownership of our workspace to a colleague?"),
    (ACCOUNT, "We need to remove a former employee's access immediately; they still have admin rights."),
    (ACCOUNT, "I get an 'account suspended' message when I sign in, but I have not violated any terms."),
    (ACCOUNT, "I received a phishing email pretending to be from you asking for my password. Reporting it."),
    (ACCOUNT, "Please merge my two accounts, one under my work email and one under my personal email."),
    (ACCOUNT, "The password reset link says it has expired every time I click it."),
    (ACCOUNT, "Can you tell me which devices are currently logged into my account? I want to sign them out."),
    # --- feedback
    (FEEDBACK, "Just wanted to say your support team was fantastic yesterday. Thank you for the quick help!"),
    (FEEDBACK, "I have been waiting 10 days for a reply to my last email and nobody has responded. This is unacceptable."),
    (FEEDBACK, "It would be great if you added a dark theme and keyboard shortcuts in the next release."),
    (FEEDBACK, "Your new pricing page is confusing and hard to read. Please rethink the design."),
    (FEEDBACK, "I am disappointed with the quality of your onboarding videos; they are outdated."),
    (FEEDBACK, "Love the new dashboard layout! Great job to the design team."),
    (FEEDBACK, "The sales rep who called me was pushy and rude. I would like to file a complaint."),
    (FEEDBACK, "Feature request: let us tag items with multiple colours and filter by them."),
    (FEEDBACK, "Your newsletter emails come too often. Please send fewer of them."),
    (FEEDBACK, "Thanks for fixing the bug I reported last week. Everything works now."),
    (FEEDBACK, "I think your documentation could use more examples, especially for beginners."),
    (FEEDBACK, "The last outage cost us half a day of work. I expect a written explanation from management."),
    (FEEDBACK, "Could you consider adding more languages? Many of our users speak Portuguese."),
    (FEEDBACK, "Your cancellation page is designed to make people give up. That is a dark pattern and I am not happy."),
]

AMBIGUOUS = [
    (BILLING, "I was charged for a plan upgrade but I still cannot use the new features."),
    (SALES, "Can I pay by invoice instead of credit card if we move to the Business plan?"),
    (ACCOUNT, "My account was charged after it got locked, and now I cannot sign in to dispute it."),
    (FEEDBACK, "The app has become so slow that I am thinking of cancelling unless it improves."),
    (ACCOUNT, "Please reset the two-factor on my account, and refund this month because I could not use it."),
    (BILLING, "The pricing page says $49 per user but checkout shows $59. Which is right?"),
    (SALES, "Do you support SAML single sign-on on the Pro plan, or only on Enterprise?"),
    (TECHNICAL, "Export to PDF is broken and I need the invoice report by tomorrow morning."),
    (BILLING, "Why was I charged for 12 seats when we only have 10 users? We also plan to add 5 more soon."),
    (ACCOUNT, "Great product, but I cannot find where to change my password."),
    (BILLING, "Hola, me cobraron dos veces este mes. Por favor devuelvan el dinero."),
    (TECHNICAL, "ok so the thing is broken again, same as last time, you know what i mean"),
]
