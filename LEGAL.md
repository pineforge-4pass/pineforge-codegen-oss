# Legal information

Summary of licensing, third-party components, and trademarks for `pineforge-codegen`. **Not** legal advice; consult counsel for your use case.

## License

`pineforge-codegen` is **source-available**, **not** OSI "open source." It is distributed under the **PolyForm Noncommercial License 1.0.0** with two supplemental terms — see [LICENSE](LICENSE), which is the controlling text:

- **Personal Trading exception** — free to research, backtest, and trade your **own** capital, for an individual acting on their own behalf.
- **Commercial use** — companies, funds, managing third-party capital, embedding in a product, or operating a hosted/public-facing service requires a **commercial license** (email **luis@4pass.com.tw**).

Describe this project as **"source-available"** rather than "open source." The runtime it targets, [`pineforge-engine`](https://github.com/pineforge-4pass/pineforge-engine), is separate and **Apache-2.0**.

## What this repository is

A pure-Python PineScript v6 → C++ transpiler that emits source against the public PineForge engine C-ABI. It implements PineScript v6 from **TradingView's publicly published language documentation**; it does **not** incorporate TradingView proprietary source. Names like "PineScript v6" are used **nominatively** to describe the input language.

## Third-party components

The transpiler has **no runtime dependencies** (one function, `transpile()`). Development/test extras declared in `pyproject.toml` (`pytest`, etc.) are under their own upstream licenses. The opt-in compile checks invoke a C++ compiler against the separately-licensed Apache-2.0 engine headers; they skip cleanly without an engine checkout.

## Trademarks and affiliation

**TradingView** and **PineScript** are trademarks of their respective owners. `pineforge-codegen` is **not** affiliated with, endorsed by, or certified by TradingView. References to "PineScript v6" and any compatibility/parity statements are **nominative** and factual — compatibility and technical testing only, not a partnership or certification.

## Contributions

Contributions are accepted under a Developer Certificate of Origin (`Signed-off-by`). Because this project is **dual-licensed** (source-available + a sold commercial license), material contributions require a Contributor License Agreement granting PineForge the right to include the contribution in the commercial license; otherwise it cannot be accepted. See `CONTRIBUTING` (or contact luis@4pass.com.tw) before opening a material PR.

## No warranty

Provided **"AS IS"**, without warranty of any kind, per the LICENSE's no-warranty / no-liability terms. Transpiler output and any downstream backtest results are **not** investment advice and carry no warranty of trading outcomes.
