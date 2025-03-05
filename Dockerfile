# Usa una imagen base de Python (puede ser 3.9, 3.10, etc.)
FROM python:3.9-slim

# Establece el directorio de trabajo
WORKDIR /app

# Copia el archivo de requerimientos y lo instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia el resto de tu código en la imagen
COPY . .

# Ejecuta tu bot de Telegram (main.py)
CMD ["python", "main.py"]
