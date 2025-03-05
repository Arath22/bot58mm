FROM python:3.9-slim

WORKDIR /app

# Copia el archivo de requerimientos y lo instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia el resto del código
COPY . .

# Ejecuta el bot
CMD ["python", "main.py"]
