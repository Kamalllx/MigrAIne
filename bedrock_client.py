import json
import os
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL_ID = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def invoke(prompt: str, max_tokens: int = 800) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required in .env")

    payload = {
        "model": MODEL_ID,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = Request(
        OPENAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urlopen(request, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8"))

    return result["choices"][0]["message"]["content"]
