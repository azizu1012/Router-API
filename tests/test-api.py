from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:58100/v1",
    api_key="sk-iiVUNH2k3QedJAroueymIo0q9qL5TimQ95vJpbNTOK4",
    timeout=60.0,
)

resp = client.chat.completions.create(
    model="gemini-flash-35",
    max_tokens=1024,
    messages=[
        {"role": "system", "content": "Bạn là trợ lý coding ngắn gọn."},
        {"role": "user", "content": "Viết hàm Python đảo chuỗi."},
    ],
)
print(resp.choices[0].message.content)
