import logging
import io
import re

import pdfplumber
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

from telegram import Update
from telegram.ext import Updater, MessageHandler, Filters, CallbackContext

# Configuración básica de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------------------
# Funciones de conversión (adaptadas de tu aplicación)
# -------------------------------

def wrap_text(text, max_width, c, font_name, font_size):
    """Envuelve el texto para que quepa en max_width usando stringWidth de ReportLab."""
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = current_line + (" " if current_line else "") + word
        if c.stringWidth(test_line, font_name, font_size) <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines

def extraer_items(lines):
    """
    Extrae los items a partir del bloque de la tabla.
    Cada item se espera que inicie con la cantidad (número decimal),
    seguido de tokens y que, al final de la descripción, pueda venir un token que sea el valor unitario.
    """
    items = []
    current_item = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r'^\d+\.\d+', line):
            if current_item is not None:
                items.append(current_item)
            tokens = line.split()
            if len(tokens) < 4:
                continue
            cantidad = tokens[0]
            token3 = tokens[2]
            m = re.match(r'^(\d+\.\d+)', token3)
            valor_unitario = m.group(1) if m else ""
            descripcion = " ".join(tokens[3:])
            desc_tokens = descripcion.split()
            if desc_tokens and re.match(r'^\d+\.\d+$', desc_tokens[-1]):
                valor_unitario = desc_tokens.pop()
                descripcion = " ".join(desc_tokens)
            current_item = {
                "cantidad": cantidad,
                "valor_unitario": valor_unitario,
                "descripcion": descripcion
            }
        else:
            if current_item is not None:
                desc_tokens = line.split()
                if desc_tokens and re.match(r'^\d+\.\d+$', desc_tokens[-1]):
                    price_candidate = desc_tokens.pop()
                    line = " ".join(desc_tokens)
                    current_item["valor_unitario"] = price_candidate
                current_item["descripcion"] += " " + line
    if current_item is not None:
        items.append(current_item)
    return items

def limpiar_header_line(line):
    """
    Limpia una línea del encabezado:
      - Si contiene la palabra "BOLETA", devuelve el texto anterior a ella.
      - Si la línea empieza con "RUC", se descarta.
      - Si contiene un patrón tipo "EB" seguido de números, se elimina ese patrón y lo que sigue.
      - En otro caso, retorna la línea.
    """
    up_line = line.upper()
    if "BOLETA" in up_line:
        index = up_line.find("BOLETA")
        return line[:index].strip()
    if up_line.startswith("RUC"):
        return ""
    m = re.search(r'(EB\d+\s*[-–]\s*\d+)', up_line)
    if m:
        return line[:m.start()].strip()
    return line.strip()

def extraer_datos_boleta(texto):
    """
    Extrae los datos clave del PDF de boleta.
    
    Se extrae el encabezado (Nombre Comercial, Razón Social, Dirección) y los siguientes campos:
      RUC, Fecha de Emisión, Número de Boleta, Cliente, Documento del Cliente y Tipo de Moneda.
    Además, se extraen los siguientes campos numéricos:
      Subtotal (Ventas), Descuentos, Valor de Venta, IGV e Importe Total.
    Los items se extraen a partir de un bloque identificado con ciertas palabras clave.
    """
    datos = {
        "nombre_comercial": "",
        "razon_social": "",
        "direccion": "",
        "ruc": "",
        "fecha_emision": "",
        "numero_doc": "",
        "cliente": "",
        "doc_cliente": "",
        "tipo_moneda": "",
        "items": [],
        "subtotal": "",
        "total": "",
        # Nuevos campos
        "descuentos": "",
        "valor_venta": "",
        "igv": ""
    }
    lines = [line.strip() for line in texto.splitlines() if line.strip()]
    header = []
    for l in lines[:6]:
        limpio = limpiar_header_line(l)
        if limpio:
            header.append(limpio)
    if header:
        datos["nombre_comercial"] = header[0]
    if len(header) >= 2:
        datos["razon_social"] = header[1]
    if len(header) >= 3:
        datos["direccion"] = "\n".join(header[2:])
    
    ruc_match = re.search(r'RUC\s*:\s*(\d+)', texto, re.IGNORECASE)
    datos["ruc"] = ruc_match.group(1) if ruc_match else ""
    fecha_match = re.search(r'Fecha\s*de\s*Emisi[oó]n\s*:\s*([\d/]+)', texto, re.IGNORECASE)
    datos["fecha_emision"] = fecha_match.group(1) if fecha_match else ""
    doc_match = re.search(r'(EB\d+\s*[-–]\s*\d+)', texto, re.IGNORECASE)
    datos["numero_doc"] = doc_match.group(1) if doc_match else ""
    m_cliente = re.search(r'Señor\s*\(es\)\s*:\s*(.+)', texto, re.IGNORECASE)
    if m_cliente:
        candidate = m_cliente.group(1).strip()
        datos["cliente"] = candidate if candidate.lower() != "null" and candidate != "" else "Clientes Varios"
    else:
        datos["cliente"] = "Clientes Varios"
    m_doc = re.search(r'(DNI|SIN DOCUMENTO)\s*:\s*([\w-]+)', texto, re.IGNORECASE)
    datos["doc_cliente"] = m_doc.group(2).strip() if m_doc else ""
    m_moneda = re.search(r'Tipo\s*de\s*Moneda\s*:\s*(\S+)', texto, re.IGNORECASE)
    datos["tipo_moneda"] = m_moneda.group(1).strip() if m_moneda else ""
    
    header_keywords = ["Cantidad", "Unidad Medida", "Código", "Valor Unitario", "Descripción"]
    header_index = None
    for i, line in enumerate(lines):
        if all(word in line for word in header_keywords):
            header_index = i
            break
    if header_index is not None:
        items_lines = []
        for line in lines[header_index+1:]:
            if re.search(r'(Sub\s*Total|Importe Total)', line, re.IGNORECASE):
                break
            items_lines.append(line)
        datos["items"] = extraer_items(items_lines)
    else:
        datos["items"] = []
    
    subtotal_match = re.search(r'Sub\s*Total\s*Ventas?\s*:\s*([\d\.]+)', texto, re.IGNORECASE)
    datos["subtotal"] = subtotal_match.group(1) if subtotal_match else ""
    total_match = re.search(r'Importe\s*Total\s*:\s*([\d\.]+)', texto, re.IGNORECASE)
    datos["total"] = total_match.group(1) if total_match else ""
    
    # Nuevos campos
    descuentos_match = re.search(r'Descuentos\s*:\s*([\d\.]+)', texto, re.IGNORECASE)
    datos["descuentos"] = descuentos_match.group(1) if descuentos_match else ""
    valor_venta_match = re.search(r'Valor\s*Venta\s*:\s*([\d\.]+)', texto, re.IGNORECASE)
    datos["valor_venta"] = valor_venta_match.group(1) if valor_venta_match else ""
    igv_match = re.search(r'IGV\s*:\s*([\d\.]+)', texto, re.IGNORECASE)
    datos["igv"] = igv_match.group(1) if igv_match else ""
    
    return datos

def generar_pdf_58mm(datos, nombre_salida):
    """
    Genera un PDF de 58 mm de ancho con la estructura de la boleta.
    Se imprime el encabezado, la identificación, la tabla de items y al final:
    
      Subtotal: S/ {subtotal}
      Descuentos: S/ {descuentos}
      Valor de Venta: S/ {valor_venta}
      IGV: S/ {igv}
      
      Importe Total: S/ {total}
      
      Gracias por su compra
    """
    ancho_hoja = 58 * mm
    alto_hoja = 300 * mm
    c = canvas.Canvas(nombre_salida, pagesize=(ancho_hoja, alto_hoja))
    
    left_margin = 3 * mm
    effective_width = ancho_hoja - 2 * left_margin
    center_x = left_margin + effective_width / 2
    y = alto_hoja - 5 * mm

    # ENCABEZADO
    if datos["nombre_comercial"]:
        font_size = 12
        while (c.stringWidth(datos["nombre_comercial"], "Helvetica-Bold", font_size) > effective_width) and (font_size > 12):
            font_size -= 1
        c.setFont("Helvetica-Bold", font_size)
        wrapped_nombre = wrap_text(datos["nombre_comercial"], effective_width, c, "Helvetica-Bold", font_size)
        for linea in wrapped_nombre:
            c.drawCentredString(center_x, y, linea)
            y -= (font_size + 2)
    if datos["razon_social"]:
        c.setFont("Helvetica", 7)
        wrapped_rs = wrap_text(datos["razon_social"], effective_width, c, "Helvetica", 7)
        for linea in wrapped_rs:
            c.drawCentredString(center_x, y, linea)
            y -= 7 
    y -= 4
    if datos["direccion"]:
        lines = datos["direccion"].splitlines()
        unique_lines = []
        for l in lines:
            if l not in unique_lines:
                unique_lines.append(l)
        def remove_line_duplication(line):
            parts = line.split(" - ")
            new_parts = []
            for part in parts:
                if not new_parts or part != new_parts[-1]:
                    new_parts.append(part)
            return " - ".join(new_parts)
        clean_lines = [remove_line_duplication(l) for l in unique_lines]
        direccion_limpia = " ".join(clean_lines)
        c.setFont("Helvetica", 7)
        wrapped_addr = wrap_text(direccion_limpia, effective_width, c, "Helvetica", 7)
        for linea in wrapped_addr:
            c.drawCentredString(center_x, y, linea)
            y -= 7

    y -= 10

    # BLOQUE DE IDENTIFICACIÓN
    id_max_width = ancho_hoja - 6 * mm
    c.setFont("Helvetica-Bold", 10)
    wrapped_title = wrap_text("BOLETA ELECTRÓNICA", id_max_width, c, "Helvetica-Bold", 10)
    for linea in wrapped_title:
        c.drawCentredString(ancho_hoja/2, y, linea)
        y -= 10
    c.setFont("Helvetica", 8)
    for linea in wrap_text(f"RUC: {datos.get('ruc', '')}", id_max_width, c, "Helvetica", 9):
        c.drawCentredString(ancho_hoja/2, y, linea)
        y -= 9
    for linea in wrap_text(datos.get("numero_doc", ""), id_max_width, c, "Helvetica", 9):
        c.drawCentredString(ancho_hoja/2, y, linea)
        y -= 9
    for linea in wrap_text(f"Fecha de Emisión: {datos.get('fecha_emision', '')}", id_max_width, c, "Helvetica", 9):
        c.drawCentredString(ancho_hoja/2, y, linea)
        y -= 9
    for linea in wrap_text(f"Señor (es): {datos.get('cliente', 'Clientes Varios')}", id_max_width, c, "Helvetica", 9):
        c.drawCentredString(ancho_hoja/2, y, linea)
        y -= 9
    if datos.get("doc_cliente"):
        for linea in wrap_text(f"DNI: {datos.get('doc_cliente')}", id_max_width, c, "Helvetica", 9):
            c.drawCentredString(ancho_hoja/2, y, linea)
            y -= 9
    if datos.get("tipo_moneda"):
        for linea in wrap_text(f"Tipo de Moneda: {datos.get('tipo_moneda')}", id_max_width, c, "Helvetica", 9):
            c.drawCentredString(ancho_hoja/2, y, linea)
            y -= 9

    y -= 10
    c.line(left_margin, y, ancho_hoja - left_margin, y)
    y -= 10

    # TABLA DE ITEMS
    col1_width = 8 * mm
    col3_width = 8 * mm
    col2_width = ancho_hoja - (left_margin + col1_width + col3_width + left_margin)
    col1_x = left_margin
    col2_x = col1_x + col1_width
    col3_x = col2_x + col2_width
    pad_col1 = 1
    pad_col2 = 2
    pad_col3 = 1
    col2_internal_width = col2_width - (2 * pad_col2)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(col1_x + pad_col1, y, "Cant")
    c.drawString(col2_x + pad_col2, y, "Descripción")
    c.drawRightString(col3_x + col3_width - pad_col3, y, "Valor")
    y -= 12
    c.setFont("Helvetica", 7)
    line_height = 8
    for item in datos["items"]:
        cantidad = item.get("cantidad", "")
        valor_unitario = item.get("valor_unitario", "")
        descripcion = item.get("descripcion", "")
        wrapped_desc = wrap_text(descripcion, col2_internal_width, c, "Helvetica", 7)
        num_lines = len(wrapped_desc)
        row_height = num_lines * line_height
        c.drawString(col1_x + pad_col1, y, cantidad)
        for i, linea in enumerate(wrapped_desc):
            c.drawString(col2_x + pad_col2, y - (i * line_height), linea)
        c.drawRightString(col3_x + col3_width - pad_col3, y, f"S/ {valor_unitario}")
        y -= row_height + 2

    y -= 5
    c.line(left_margin, y, ancho_hoja - left_margin, y)
    y -= 10

    # Totales y nuevos campos (Formato final)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(left_margin, y, f"Subtotal: S/ {datos.get('subtotal', '')}")
    y -= 12
    c.drawString(left_margin, y, f"Descuentos: S/ {datos.get('descuentos', '')}")
    y -= 12
    c.drawString(left_margin, y, f"Valor de Venta: S/ {datos.get('valor_venta', '')}")
    y -= 12
    c.drawString(left_margin, y, f"IGV: S/ {datos.get('igv', '')}")
    y -= 14
    c.drawString(left_margin, y, f"Importe Total: S/ {datos.get('total', '')}")
    y -= 14

    # Mensaje final
    c.setFont("Helvetica", 7)
    c.drawCentredString(ancho_hoja/2, y, "Gracias por su compra")
    y -= 10

    c.showPage()
    c.save()

def convertir_boleta_sunat_58mm(pdf_entrada, pdf_salida):
    try:
        with pdfplumber.open(pdf_entrada) as pdf:
            texto_completo = ""
            for page in pdf.pages:
                texto_completo += page.extract_text() + "\n"
        datos = extraer_datos_boleta(texto_completo)
        generar_pdf_58mm(datos, pdf_salida)
        return True, "Conversión exitosa"
    except Exception as e:
        return False, str(e)

# -------------------------------
# Código del Bot de Telegram
# -------------------------------

# Tu API token (mantén este token en secreto)
TOKEN = "6187469734:AAH9jaVBcWRKoeP35n5w_ye0eyMwcjhKIw0"

def document_handler(update: Update, context: CallbackContext):
    document = update.message.document
    if not document or document.mime_type != "application/pdf":
        update.message.reply_text("Por favor, envía un archivo PDF válido.")
        return

    update.message.reply_text("Procesando tu PDF, por favor espera...")
    try:
        # Descargar el archivo enviado en un objeto BytesIO
        file_obj = document.get_file()
        file_bytes = io.BytesIO()
        file_obj.download(out=file_bytes)
        # Convertir el PDF usando la misma lógica
        file_bytes.seek(0)
        output_pdf = io.BytesIO()
        success, result = convertir_boleta_sunat_58mm(file_bytes, output_pdf)
        if success:
            output_pdf.seek(0)
            update.message.reply_document(document=output_pdf,
                                            filename="boleta_58mm.pdf",
                                            caption="Aquí tienes tu boleta en formato 58mm.")
        else:
            update.message.reply_text("Error al convertir el PDF:\n" + result)
    except Exception as e:
        logger.exception("Error durante la conversión")
        update.message.reply_text("Ocurrió un error inesperado durante la conversión.")

def start_handler(update: Update, context: CallbackContext):
    update.message.reply_text("¡Hola! Envíame un archivo PDF de boleta y te devolveré la versión en formato 58mm.")

def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(MessageHandler(Filters.document, document_handler))
    dp.add_handler(MessageHandler(Filters.text, start_handler))

    updater.start_polling()
    logger.info("Bot iniciado.")
    updater.idle()

if __name__ == '__main__':
    main()
