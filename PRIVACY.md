# Privacy Policy — health-metrics-service

**Last updated:** 2026-05-14
**Operator:** Hugo Delgado (`hdelgad2@alumni.nd.edu`)

## What this app is

`health-metrics-service` is a single-user personal application operated by Hugo Delgado. It ingests his own health-and-fitness data from third-party services (Oura, Whoop) into a private database for personal analysis and decision-support.

## Data collected

Via authenticated API access using the operator's own credentials:

- **Oura:** sleep, HRV, resting heart rate, body temperature deviation, readiness scores
- **Whoop:** recovery score, HRV, RHR, sleep performance, day strain, workout sessions

Manually entered by the operator:

- Body weight, nutrition (kcal / macros), subjective markers (energy, mood, hunger)

## How data is used

- Stored in a private Postgres database accessible only to the operator
- Surfaced to the operator through a personal dashboard
- Made available to the operator's AI assistant (Anthropic Claude, via the Model Context Protocol) for analytical and decision-support purposes at the operator's explicit request

## How data is shared

- **No third-party sharing.** The application does not share, sell, or transmit data to anyone other than the original data sources (Oura, Whoop) and the operator's own AI tooling.
- **Outbound network requests** are made only to: Oura API, Whoop API, and Anthropic API. All requests are initiated by the operator's own actions or scheduled jobs running under the operator's account.

## Retention

Data is retained for the operating lifetime of the system to support long-term trend analysis. The operator may delete the database at any time without notice.

## Security

- Database access is restricted to the application service and its operator
- API credentials (Oura personal access token, Whoop refresh token) are stored in environment variables and never committed to source control

## Changes to this policy

Updates to this policy will be reflected in the file at this URL with the "Last updated" date adjusted accordingly.

## Contact

Hugo Delgado — `hdelgad2@alumni.nd.edu`
