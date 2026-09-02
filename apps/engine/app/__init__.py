"""SSR pipeline workers — not implemented yet.

Per the architecture doc, this app will own: reaction -> embed -> cosine ->
shift -> normalize -> expected value -> penalty, the Bradley-Terry ranking
computation, and driving the report-synthesis call. Stateless, horizontally
scalable, coordinated by Temporal (owns retries and the results-gate pause).

See ../README.md for status and what a first real slice would need.
"""
