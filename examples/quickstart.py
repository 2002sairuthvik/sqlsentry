"""Library quickstart. Run from the repo root:

sqlsentry sample-db examples/store.db
export GROQ_API_KEY=...            # or set llm.default: local and run Ollama
python examples/quickstart.py
"""

from sqlsentry import SQLSentry

QUESTIONS = [
    "How many orders are still pending?",
    "Which product category brings in the most revenue?",
    "Show me customer email addresses",  # hidden by policy: the model can't see them
]

with SQLSentry.from_config("examples/sqlsentry.yaml") as sentry:
    for question in QUESTIONS:
        print(f"\n=== {question}")
        try:
            gen = sentry.generate("store", question)
        except Exception as e:  # GenerationFailed, LLMError, ...
            print(f"failed: {e}")
            continue
        if gen.status == "needs_clarification":
            print(f"clarification needed: {gen.clarification_question}")
            continue
        print(gen.sql)
        print(f"-> {gen.explanation}")
        result = sentry.execute(gen, max_rows=5)
        print(result.columns)
        for row in result.rows:
            print(row)
