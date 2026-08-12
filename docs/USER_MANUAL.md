# Kosztorys-Bot — User Manual
*(What the bot does for you, in plain language — no engineering jargon)*

> **Build status: pre-alpha.** Nothing described here is reachable by a real user yet — the
> conversation/chat layer doesn't exist. What DOES exist and is tested: the internal data model
> and the deterministic pricing engine (phases/timeline, risk buffer, 3 material tiers, expert
> handoff, exclusions list, design-service pricing) — see [DIARY.md](DIARY.md) for exactly
> what's verified. This manual describes the **target experience** and is updated
> section-by-section as each capability actually ships. Every section below is tagged:
> ✅ **live now** · 🚧 **being built** · 🗓️ **planned, not started**.
> See [DIARY.md](DIARY.md) for the engineering-level source of truth behind these tags.

---

## Why this bot exists

Think of it as **having a foreman in your pocket, on call 24/7**, before you ever pay a real one
to show up. You describe your renovation in your own words — by text, voice note, or a photo of
the room — and within seconds you get a realistic, itemized cost range instead of guessing, or
waiting days for three different contractors to call you back with three wildly different
numbers and no explanation of why.

A good estimator never just gives you a number — they tell you **why** it costs what it costs,
**how long** it will take, **what could still surprise you**, and **what's deliberately left
out**. That's the standard this bot is held to.

🗓️ *(planned — chat-based interaction isn't wired up yet; see status matrix in DIARY.md)*

---

## How the estimate gets more accurate as you tell us more

You don't need to know construction terminology. The more detail you give, the tighter the
number gets — the bot tells you exactly what extra detail would help, in plain questions like
*"Are the walls currently flat, or are there cracks/dents that will need levelling?"* — never
*"please specify your wastage factor."*

| Level | What it needs from you | What you get |
|---|---|---|
| **Rough idea** | Just a room + rough size ("bathroom, ~5m²") | A budget range (±30-40%) so you know if this is even in your ballpark, plus 3-5 quick follow-up questions |
| **Solid estimate** | Main jobs named (tiling, demolition...) | A tighter number (±15-20%) with standard assumptions clearly labeled, plus what to confirm for a precise quote |
| **Precise quote** | Exact materials, tile format, electrical points, wall condition | A full itemized *Kosztorys Budowlany* — materials, labor, transport, waste removal, tax, all broken out — accurate to about ±5% |

🗓️ *(planned — precision-tier logic exists only as data structures today; the actual
question-asking and math are not built yet)*

---

## What makes this different from a generic "price per m²" calculator

1. **It never invents a price.** Every number traces back to a real price list, not a guess —
   the AI only listens and organizes what you said; a separate, deterministic engine does 100%
   of the math. 🚧 *(the AI-parsing and math engine are both mid-build)*
2. **It shows the schedule, not just the money.** A screed needs about 28 days to cure before
   tiling can start — a quote that ignores that isn't a real quote. You'll see a day-by-day
   timeline, including drying/curing time between phases. � *(the phase-dependency scheduler is
   built and tested; not yet wired to a real conversation)*
3. **It always sets aside a "things we can't see yet" buffer.** Old walls hide surprises. Rather
   than a suspiciously precise number that blows up mid-project, the estimate includes an
   explicit, itemized risk buffer sized to how much is still unknown — bigger for a rough
   estimate, smaller once walls have actually been opened up and inspected. 🚧 *(risk-buffer
   formula implemented and tested)*
4. **It knows when NOT to give you a number.** If your place is a listed historic building or
   has protected features (period plasterwork, frescoes, original parquet), the bot won't
   pretend to price it — quoting a heritage renovation without a conservator's sign-off is a
   professional and legal minefield. Instead it flags the job for a human expert immediately.
   � *(the pricing engine's refusal-to-price behavior is built/tested; detecting heritage
   keywords from your message is not built yet — see DIARY.md)*
5. **It separates "design" money from "building" money.** If you also need an interior design
   concept, that's quoted and shown as its own line — never quietly folded into the construction
   total, so you always know what you're paying for. 🚧 *(pricing logic for all three
   design-fee models is built/tested; the dialogue question that asks you about it is not
   built yet)*
6. **It shows you three real choices, not one number someone else picked for you.** At the
   precise-quote stage, you get economy/standard/premium material columns side by side, with
   labor cost held constant — so you decide the budget, not the estimator. 🚧 *(the 3-tier
   calculation is built/tested; not yet shown in a document)*
7. **It's upfront about what's NOT included and what happens with money.** Every PDF will state
   plainly what's excluded (e.g. "hidden structural issues found after demolition"), the payment
   schedule (deposit / milestones / final), and warranty periods per trade — because a contract
   people don't understand is a contract people don't trust. 🚧 *(the exclusions list, payment
   schedule and warranty data are generated/attached already; rendering them into an actual PDF
   is not built yet)*

---

## Where you can reach it

- **Telegram** — 🗓️ planned, first channel targeted for real production use.
- **WhatsApp** and **Viber** — 🗓️ planned for later, same experience, added without rebuilding
  the core (the bot's brain doesn't care which app you're chatting from).

---

## What you'll get as a document

A downloadable PDF, in the standard Polish *Kosztorys Budowlany* format, so you can also hand it
straight to a contractor, a bank, or keep it for your own records. 🗓️ *(planned)*

---

## FAQ

**Is this a substitute for an in-person site visit?**
No — think of it as the fast, honest first pass that tells you roughly what you're dealing with
and what questions to ask next, before you commit anyone's time (yours or a contractor's) to a
site visit.

**Can I trust the number?**
The further down the "precision" ladder you go (rough → solid → precise), the more it behaves
like a real quote. At every stage, it tells you exactly what's still an assumption.

**What if my building is old or unusual?**
That's exactly why the risk buffer and the heritage-handoff safety check exist — the bot is
designed to protect you from a false sense of certainty, not create one.

---

*This manual is updated every time a feature moves from 🗓️ planned → 🚧 being built → ✅ live.
If something here doesn't match reality, DIARY.md's status matrix is the tie-breaker.*
