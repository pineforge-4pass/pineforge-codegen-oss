# Legal information

Summary of licensing, third-party components, and trademarks for `pineforge-codegen`. **Not** legal advice; consult counsel for your use case.

## License

`pineforge-codegen` is **source-available**, **not** OSI "open source." It is distributed under the **PineForge Source License 1.2** — see [LICENSE](LICENSE), which is the controlling text.

- **Noncommercial use** — any noncommercial purpose, and use by a charitable organization, educational institution, public research organization, public safety or health organization, environmental protection organization or government institution for its teaching, research and other operations, is free.
- **Personal Trading** — free for a natural person to research, develop or backtest strategies and trade their **own** account with their **own** capital. Household, joint and retirement accounts and ordinary margin count as their own; a company's or fund's account does not, even a company they wholly own. An account that a proprietary-trading firm or funded-trader program provides or allocates to them, including a challenge, evaluation or simulated account, does not count as their own either.
- **Investment management** — managing, advising on or trading investment capital, or researching strategies for it (a friend's, clients' or investors' capital, an endowment, a pension fund, a public fund, a foundation's treasury, or an account a proprietary-trading firm or funded-trader program provides or allocates, including challenge, evaluation and simulated accounts), is **Commercial Use for every individual and every organization**, noncommercial organizations included, except Personal Trading.
- **Commercial Use** — investment management and any other use that is not free, such as use by or for a company or fund, embedding the software or its output in a product or service for others, or operating a hosted/public-facing service with it, requires a **commercial license** (email **enterprise@pineforge.dev**; the commercial-license store is coming soon).

Describe this project as **"source-available"** rather than "open source." The runtime it targets, [`pineforge-engine`](https://github.com/pineforge-4pass/pineforge-engine), is separate and **Apache-2.0**.

Releases up to and including 1.1.0 were published under the license text that came with them (the PolyForm Noncommercial License 1.0.0 with a PineForge supplement); copies of those releases keep that license. The PineForge Source License is a separate license and is not a PolyForm license.

The PineForge Source License 1.1 replaced 1.0, and 1.2 replaces 1.1, for the code on `main` and in future releases. Version 1.1 clarified one point, that distributing the software or its output, changed or not, embedded in or bundled with a product or service made available to others is Commercial Use, not free distribution, unless it is for a permitted purpose. Version 1.2 makes two points explicit: an account a proprietary-trading firm or funded-trader program provides or allocates, including a challenge, evaluation or simulated account, is not a person's own account or capital, so trading it is not Personal Trading; and the capital in such an account, real or simulated, is investment capital, so trading it is investment management, which is Commercial Use. It changes nothing else. Release 1.2.0 was published under 1.1 and keeps that text.

## Copyright and licensor

The licensor is **pineforge, LLC**, a Delaware limited liability company. It holds the copyright in `pineforge-codegen`; the founder's rights in the software are assigned to it. Commercial licenses are granted by pineforge, LLC.

## What this repository is

A pure-Python PineScript v6 → C++ transpiler that emits source against the public PineForge engine C-ABI. It implements PineScript v6 from **TradingView's publicly published language documentation**; it does **not** incorporate TradingView proprietary source. Names like "PineScript v6" are used **nominatively** to describe the input language.

## Third-party components

The transpiler has **no runtime dependencies** (`transpile()` and `transpile_full()` are its supported Python entry points). Development/test extras declared in `pyproject.toml` (`pytest`, etc.) are under their own upstream licenses. The opt-in compile checks invoke a C++ compiler against the separately-licensed Apache-2.0 engine headers; they skip cleanly without an engine checkout.

## Trademarks and affiliation

**TradingView** and **PineScript** are trademarks of their respective owners. `pineforge-codegen` is **not** affiliated with, endorsed by, or certified by TradingView. References to "PineScript v6" and any compatibility/parity statements are **nominative** and factual — compatibility and technical testing only, not a partnership or certification.

## Contributions

Contributions are accepted under a Developer Certificate of Origin (`Signed-off-by`). Because this project is **dual-licensed** (source-available + a sold commercial license), material contributions require a Contributor License Agreement granting pineforge, LLC the right to include the contribution in its commercial licenses; otherwise it cannot be accepted. See [CONTRIBUTING.md](CONTRIBUTING.md) (or contact luis@4pass.com.tw) before opening a material PR.

## No warranty

Provided **"AS IS"**, without warranty of any kind, per the LICENSE's no-warranty / no-liability terms. Transpiler output and any downstream backtest results are **not** investment advice and carry no warranty of trading outcomes.
