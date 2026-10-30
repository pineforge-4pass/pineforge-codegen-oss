# Engine drift
The scheduled and manual-dispatch job runs the focused host-member and compile sweep checks.
It checks out codegen at its default ref and the engine repository at `main`.
The floating engine checkout shows whether the next engine change will drift.
A red run means codegen does not compile or behave against that engine revision.
Read the step summary for the engine SHA, the committed pin, and the result.
Use the retained compile report to identify the first failing strategy or check.
The owner who syncs codegen to a newer engine bumps `ci/engine-pin`.
That reviewed bump also carries any required codegen and generated-member fixes.
