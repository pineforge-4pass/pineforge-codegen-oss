> **DRAFT — requires review by counsel before go-live**

This is a template prepared for review by counsel. It is not legal advice, and
it is not yet offered to anyone: no one can accept it until counsel has
reviewed it and this notice is removed. Text in square brackets is a fact or a
choice that the owner or counsel still has to supply.

# PineForge Codegen — Commercial License Agreement

Version: draft-2026-10-04

## 1. Parties, Orders and acceptance

**1.1 Parties.** This agreement is between PineForge, acting through
[LICENSOR LEGAL ENTITY — owner to confirm], whose address is
[LICENSOR ADDRESS — owner to confirm] ("**Licensor**"), and the organization
named on the Order ("**Licensee**"). Licensor is the licensor named in the
LICENSE, or sells this license with that licensor's authority
[CHAIN OF TITLE — counsel to confirm].

**1.2 Orders.** An Order is either the record of a self-serve checkout or a
quote that both parties have signed. It states Licensee's name and address, the
Tier, the Option, any Affiliates it covers, the fees and their currency, the
Term, and the version of this agreement that applies. Licensee chooses its Tier
and Option and is responsible for choosing ones that cover its use.

**1.3 Acceptance.** Licensee accepts this agreement by completing the checkout
for an Order or by signing a quote. The person doing so confirms that they are
authorized to bind Licensee. The license starts only when Licensor has received
payment and issued the License Certificate (section 10), unless a signed quote
says otherwise.

## 2. Definitions

The LICENSE's own words "licensor", "software" and "you" correspond to
Licensor, the Software and Licensee. In this agreement:

- **Affiliate**: any organization that has control over, is under the control
  of, or is under common control with Licensee. "Control" has the LICENSE's
  meaning: ownership of substantially all the assets of an entity, or the power
  to direct its management and policies by vote, contract, or otherwise,
  whether direct or indirect. (This mirrors the LICENSE's "your company".)
- **AUM**: the market value, in the currency of the Order, of capital belonging
  to others that Licensee and the Affiliates the Order covers manage, advise on
  or trade using the Software or its Output, valued as Licensee values it for
  its own investor or client reporting. AUM counts only capital managed with
  the Software, not all capital Licensee manages
  [AUM BASIS — owner to confirm]. AUM is measured on the date of the Order and
  on the date of each renewal Order. During the Term, a rise above the AUM band
  counts only if new capital caused it [MID-TERM AUM — owner to confirm]; a
  rise from market movement alone waits for the next renewal.
- **AUM band**: the range of AUM, with its upper limit, that the Order lists.
- **Deployment Scope**: the Products named on the Order and, for each, the
  number or band of End Users the Order allows.
- **End User**: a person or organization, other than Licensee and its
  Personnel, that uses a Product.
- **License Certificate**: the signed record of an Order that Licensor issues
  after payment (section 10): a license id and the Order's key terms, signed
  with an Ed25519 key, that can be checked online.
- **License Site**: https://license.pineforge.dev, or any address Licensor later
  gives for it.
- **LICENSE**: the file named LICENSE published with the Software: the PolyForm
  Noncommercial License 1.0.0 (its "base license"), with the Additional
  Permission — Personal Trading and Commercial Use sections, as it reads on the
  date of the Order.
- **Licensed Uses**: has the meaning in section 4.1.
- **Option**: the size an Order chooses within a Tier: Seats (Team); an AUM
  band and Seats (Fund); a Deployment Scope (OEM / Embedded).
- **Order**: the record of the license Licensee takes under this agreement
  (section 1.2).
- **Output**: the C++ source code the Software generates from PineScript, and
  any object code or executable built from it.
- **Personnel**: Licensee's employees and the individual contractors who work
  under its direction.
- **Product**: a product, application, platform or service that is named on the
  Order and made available to others, and that either embeds the Software or
  its Output or is a hosted, software-as-a-service or other public-facing
  service that uses the Software.
- **Required Notice**: a plain-text line beginning `Required Notice:` that the
  LICENSE provides.
- **Seat**: a named individual who uses the Software for Licensee, such as a
  member of its Personnel. Use by an automated process (for example a CI job)
  counts as use by the individual responsible for it. A person who only runs
  compiled Output, and does not run, configure or modify the Software, needs no
  Seat. A Seat may move to another individual when its holder stops working for
  Licensee or no longer needs it; Seats may not be shared or rotated to get
  round the number on the Order.
- **Software**: pineforge-codegen, the PineScript v6 to C++ transpiler published
  at https://github.com/pineforge-4pass/pineforge-codegen-oss, in each version
  and form Licensor publishes it. It does not include pineforge-engine, the
  separate runtime that Output is built against, which is under its own license.
- **Term**: the period for which an Order is in force: 12 months from the start
  date on its License Certificate, unless the Order says otherwise.
- **Tier**: Team, Fund or OEM / Embedded (OEM for short), as the Order states.

## 3. How this agreement relates to the LICENSE

**3.1 The LICENSE stays.** The Software remains available to everyone under the
LICENSE. This agreement adds rights; it does not take away any permission the
LICENSE gives. In particular, an individual's Personal Trading (their own
account, their own capital) stays free, whether or not any organization they
work for holds an Order.

**3.2 What this agreement is.** The LICENSE's Commercial Use section requires "a
separate commercial license from the licensor" for any use that is not a
permitted purpose under the base license and is not covered by the Personal
Trading permission, including the four uses listed in its Additional Permission
section. This agreement, together with an Order, is that license for the uses
the Order's Tier covers (section 4), and for no other use. The four uses, quoted
from the LICENSE without change and called "use (1)" to "use (4)" below, are:

> (1) managing, advising on, or trading capital belonging to any other person
> or entity, whether or not for a fee;
>
> (2) use by, for, or on behalf of any company, fund, partnership, or other
> organization, including use by an individual in the course of work for such
> an organization;
>
> (3) embedding the software, or output generated by it, into any product or
> service made available to others; or
>
> (4) operating any hosted, software-as-a-service, or otherwise public-facing
> service that uses the software.

**3.3 Other uses.** A use the Order's Tier does not cover stays Commercial Use
under the LICENSE and needs another Order or custom terms (section 4.5). The
LICENSE's other terms, including Notices, Patent Defense and Violations,
continue to apply to the Licensed Uses. Section 14.2 sets the order of
precedence.

**3.4 Contact.** Licensor's contact for commercial licenses is
enterprise@pineforge.dev. It applies wherever the LICENSE names a different
address for obtaining a commercial license.

## 4. The license

**4.1 Common terms.** Subject to this agreement, payment and the limits on the
Order, Licensor grants Licensee, for the Term, a non-exclusive license for the
uses the Order's Tier covers (the "**Licensed Uses**", sections 4.2 to 4.4).
Each Licensed Use is a permitted purpose under the LICENSE for Licensee during
the Term, including its Copyright License, Changes and New Works License and
Patent License (and, for the OEM / Embedded Tier, its Distribution License
within section 4.4). So, for the Licensed Uses, Licensee may run, copy and
modify the Software and generate, compile, run and modify Output. In sections
4.2 to 4.4 and 6, "Licensee" includes the Affiliates the Order covers, and
their Seats, AUM and Deployment Scope count together against the Order's
limits. The license cannot be transferred or sublicensed except as sections 4.4
and 5 say. No support, maintenance or service level is included unless the
Order says so.

**4.2 Team.**

- *Licensed Uses:* use (2): use by, for or on behalf of Licensee, including use
  by its Personnel in the course of their work for it, for internal research,
  development, backtesting and trading of Licensee's own capital.
- *Limit:* the number of Seats on the Order.
- *Not included:* uses (1), (3) and (4).

**4.3 Fund.**

- *Licensed Uses:* uses (1) and (2): managing, advising on or trading capital
  belonging to others, by Licensee, with AUM within the AUM band on the Order,
  and the uses described for the Team Tier. Use is internal only: Licensee may
  share the results of its use (for example reports, performance figures and
  advice) with its clients and investors, but may not give them the Software or
  Output, let them run either, or put either in a product or service made
  available to them.
- *Limits:* the AUM band and the number of Seats on the Order.
- *Not included:* uses (3) and (4).

**4.4 OEM / Embedded.**

- *Licensed Uses:* uses (3) and (4), and use (2) as needed to build and run the
  Products: embedding the Software or Output in a Product made available to
  others, and operating a Product that is a hosted, software-as-a-service or
  other public-facing service that uses the Software.
- *Limit:* the Deployment Scope on the Order. A product or service not named on
  the Order is outside it (section 6.2).
- *End Users:* Licensee may let End Users use a Product. This agreement gives
  End Users no right to the Software itself: apart from using the Product as
  Licensee makes it available, it does not let them use, run or copy the
  Software, or extract or reuse its code from the Product.
- *Not included:* use (1); Licensee's own research and trading beyond building
  and running the Products (that is the Team Tier); and redistributing or
  offering the Software as a standalone product, transpiler or API. A Product
  whose principal function is to give others the use of the Software's
  PineScript-to-C++ transpilation, such as a conversion tool or API, is such an
  offering.

**4.5 Beyond the Tiers.** A Licensee that needs more than one Tier places an
Order for each; each Order's limits apply to that Order alone. Anything beyond
the Tiers and their Options (for example more Seats, a higher AUM band or a
larger Deployment Scope than the checkout offers) is available only on custom
written terms in a signed quote. Email enterprise@pineforge.dev.

## 5. Restrictions

These add to the LICENSE's restrictions, including its "No Other Rights"
section. Licensee will not, and will not let anyone else:

- **5.1** sublicense the Software, except that Licensee may let End Users use a
  Product as section 4.4 says;
- **5.2** transfer this agreement, an Order or a License Certificate, except
  together with the whole business that uses the Software, to a successor that
  agrees in writing to be bound by this agreement, on written notice to
  Licensor;
- **5.3** remove or hide the Required Notice, or give anyone a copy of any part
  of the Software without the LICENSE's terms (or their URL) and the Required
  Notice, as the LICENSE's Notices section requires;
- **5.4** compete with Licensor by reselling the Software: offering it or its
  transpilation as a standalone product, transpiler or API (section 4.4);
- **5.5** use the Software beyond the Tier and limits on the Order, or split
  capital, Seats, Products or End Users among entities or Orders to fit a lower
  Option;
- **5.6** state or imply that Licensor endorses Licensee or a Product, or that
  Licensor or the Software is affiliated with, endorsed by or certified by
  TradingView. This agreement grants no right in any trademark.

## 6. Changes in scope

**6.1 Upgrade Order.** If Licensee's Seats, AUM or End Users grow beyond what
the Order allows, Licensee places an upgrade Order within [30] days after the
day the limit was passed. Licensor will not treat the growth as a breach if the
upgrade Order is placed and paid for within that period. After it, use above
the Order's limits is outside the license and section 13 applies. An upgrade
Order states its own fee and Term.

**6.2 New Products and Tiers.** A Product not named on the Order, and a use the
Tier does not cover, are not growth: Licensee needs an Order for them before
they start.

**6.3 Records.** Licensee keeps records that show its Seats, AUM and Deployment
Scope, and confirms in writing, within [30] days of Licensor's reasonable
written request, that they are within the Order.

## 7. Fees, invoices and taxes

**7.1 Fees.** Licensee pays the fees on the Order. Annual fees are prepaid for
the Term. The prices on the Order are the only prices that apply; this
agreement states none. A change in Licensor's prices does not affect an Order
already placed until it is renewed.

**7.2 Payment and invoices.** Payment is taken through Stripe at checkout, in
the currency on the Order, and Licensee authorizes the charge. Licensor issues
an invoice for each annual plan. For a signed quote, the quote says how and
when payment is due.

**7.3 Taxes.** Applicable taxes (such as VAT, GST or sales tax) are added where
the law requires them; Stripe Tax may calculate them at checkout. Licensee is
responsible for taxes the law puts on it, other than taxes on Licensor's
income.

## 8. Term, renewal and expiry

**8.1 Term.** An Order runs for its Term: 12 months from the start date on its
License Certificate, unless the Order says otherwise.

**8.2 Renewal.** An Order renews only by a new Order; there is no automatic
renewal unless the Order says so. A renewal Order measures AUM again. Licensor
may publish a new version of this agreement: an Order stays under the version
that applies when it is placed, and a renewal Order is under the version and
the prices current when it is placed.

**8.3 Expiry.** When the Term ends, the Licensed Uses end. Licensee must stop
them, including making any Product available, unless it has renewed.
[WIND-DOWN PERIOD — owner to decide; delete if none]

**8.4 What expiry leaves alone.** Expiry does not affect the Personal Trading
permission or any other permission of the LICENSE, which every individual and
organization keeps on the LICENSE's own terms.

## 9. Refunds and revocation

**9.1 Refunds.** [REFUND POLICY — owner to provide]

**9.2 Revocation.** A full refund of an Order, or a chargeback on it (a payment
dispute decided or accepted in the payer's favor), revokes its License
Certificate and the commercial rights the Certificate evidences, from the date
of the refund or chargeback. Licensor will mark the license as revoked on the
License Site, and section 8.3 applies as if the Term had ended.

## 10. License Certificate and verification

**10.1 Issue.** After payment (or as a signed quote provides), Licensor issues
a License Certificate for the Order. It is signed with Ed25519 and carries a
license id and the Order's key terms. It evidences the Order and does not widen
it: if they differ, the Order controls.

**10.2 Online check.** Anyone may check a license id on the verification page
of the License Site. Licensee understands that anyone who holds its license id
can see what that page shows for it.

**10.3 Custody.** Licensee keeps its License Certificate and may show it to
anyone who asks to see its commercial license. Licensee may not alter a
Certificate or present one issued for another Order or Licensee. Licensor will
reissue a lost Certificate on request to enterprise@pineforge.dev.

## 11. Ownership

**11.1** Licensor keeps all rights in the Software that this agreement does not
expressly grant.

**11.2** Licensee keeps its PineScript source, strategies, data and trading
results, and Licensor claims no ownership of Licensee's strategy logic as
expressed in Output [OUTPUT OWNERSHIP — counsel to confirm]. To the extent
Output contains material from the Software, it is licensed under section 4 for
the Licensed Uses.

## 12. Warranty, liability and trading

**12.1 As is.** As far as the law allows, the Software comes as is, without any
warranty or condition, as the LICENSE says. Licensor does not warrant that
Output behaves in any other environment, including TradingView, as the same
script does.

**12.2 No advice.** The Software is a code generator. Output and any backtest
results are not investment advice and carry no warranty of trading outcomes.
Licensee alone is responsible for its trading and advisory decisions, for
meeting the regulatory obligations that apply to it, and for testing Output
before relying on it.

**12.3 Liability.** As far as the law allows, Licensor is not liable for
trading losses, lost profits, loss of data, or indirect or consequential loss,
and Licensor's total liability under or in connection with this agreement,
under any kind of legal claim, will not exceed
[LIABILITY CAP — amount to be set by counsel]. This section works with the
LICENSE's No Liability section, which also applies. Nothing in this agreement
limits liability that the law does not allow to be limited.

## 13. Termination and survival

**13.1 Breach.** If Licensor notifies Licensee in writing that Licensee has
breached this agreement, the rights under this agreement continue if, within 32
days of receiving the notice, Licensee comes into full compliance and takes
practical steps to correct past violations (the same period as the LICENSE's
Violations section). If it does not, those rights end when the 32 days end.

**13.2 By Licensee.** Licensee may end this agreement at any time by written
notice.

**13.3 Effect.** When the rights under this agreement end for any reason,
section 8.3 applies. Fees already due remain payable, and ending this agreement
gives no refund except as section 9.1 provides. Ending it does not remove
permissions the LICENSE gives independently of it; a breach of the LICENSE
itself is dealt with under the LICENSE's Violations section.

**13.4 Survival.** Sections 8.4, 11, 12, 13 and 14, and any term that by its
nature continues, survive the end of this agreement.

## 14. General

**14.1 Entire agreement.** This agreement, the Order and the LICENSE are the
entire agreement between the parties about the Software and replace earlier
proposals and quotes about it.

**14.2 Order of precedence.** For the Licensed Uses, if these documents
conflict, the Order controls over this agreement, and this agreement controls
over the LICENSE. This does not reduce any permission the LICENSE gives for any
other use.

**14.3 Changes.** Only a writing signed by both parties changes this agreement
or an Order. Section 8.2 deals with new versions.

**14.4 Notices.** Notices to Licensor go by email to enterprise@pineforge.dev;
a notice of breach or termination also goes to
[NOTICE ADDRESS — owner to provide]. Notices to Licensee go to the email
address on the Order. An email notice is received when it is sent, unless the
sender gets a delivery-failure message.

**14.5 Transfer by Licensor.** Licensor may transfer this agreement to a
successor to the Software business and will tell Licensee.

**14.6 No third-party rights.** Only the parties, and a successor permitted by
section 5.2 or 14.5, have rights under this agreement. Affiliates and End Users
have none of their own.

**14.7 Compliance with law.** Each party will comply with the laws that apply to
it under this agreement, including export-control and sanctions laws. Licensor
may refuse or revoke an Order where those laws require it.

**14.8 Governing law and venue.** [GOVERNING LAW AND VENUE — to be set by counsel]

**14.9 Severability and waiver.** If a term is unenforceable, the rest stands.
Not enforcing a term is not a waiver of it.

## Schedule A — Tiers at a glance

Section 4 governs; this table summarizes it. Prices are on the Order, not here.

| Tier | Permitted uses (LICENSE numbers) | Limit | Not included |
| --- | --- | --- | --- |
| **Team** | (2): use by, for or on behalf of Licensee and its Personnel, for internal research, development, backtesting and trading of Licensee's own capital | Seats | (1), (3), (4) |
| **Fund** | (1) and (2): managing, advising on or trading capital belonging to others, internal use only, plus the Team uses | AUM band and Seats | (3), (4) |
| **OEM / Embedded** | (3) and (4), plus (2) as needed to build and run the Products: embedding the Software or Output in named Products, or operating a hosted, software-as-a-service or public-facing Product that uses the Software | Deployment Scope (Products × End Users) | (1); Licensee's own research and trading beyond the Products; redistributing or offering the Software as a standalone product, transpiler or API; End Users get no right to the Software itself |
| **Beyond the bands** | Whatever a signed quote states (section 4.5) | Per the quote | Per the quote |

An individual's Personal Trading with their own capital stays free under the
LICENSE and is not a Tier.
