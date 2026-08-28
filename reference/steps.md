The SSR Engine — 8 steps + 2 post-loop steps

Pre-loop (runs once):

Embed all 5 anchor statements using ada-002 → stored in memory as vectors

Main loop (repeats for every pair × persona × respondent):

LLM Response — GPT-4.1 generates a 3-5 sentence free text paragraph from the doctor's perspective
Response Embedding — ada-002 converts that paragraph into a 1536-dimensional vector
Cosine Similarity — pure numpy math, compares response vector against each of 5 anchor vectors → 5 scores
Adjusted Scores — subtract minimum + add 0.02 floor → remove baseline noise
PMF Normalize — divide by sum → 5 probabilities that add to 100%
Mean SSR — weighted average → one number between 1 and 5
Apply Penalties — scan free text for trigger words → add adjustment → Final SSR
Record Win — compare Claim A final SSR vs Claim B final SSR → lower wins → update wins matrix

Post-loop (runs once after all iterations):

Bradley-Terry — 500 iterations on the wins matrix → normalized strength scores →

ranking
Aggregate — win rate, mean SSR, high intent % per claim and per persona