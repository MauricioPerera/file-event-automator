import sys
import time
from pathlib import Path

inbox = Path("./demo/inbox")

def test_csv():
    print("[+] Creando factura_001.csv en demo/inbox/...")
    csv_file = inbox / "factura_001.csv"
    csv_file.write_text("id,cliente,monto,moneda\n101,Acme Corp,4500,USD\n102,Stark Ind,12300,USD\n103,Wayne Ent,8900,USD\n", encoding="utf-8")
    print(f"  -> Creado: {csv_file.name} ({csv_file.stat().st_size} bytes)")

def test_image():
    print("[+] Creando logo_empresa.png en demo/inbox/...")
    img_file = inbox / "logo_empresa.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    print(f"  -> Creado: {img_file.name} ({img_file.stat().st_size} bytes)")

def test_delete():
    print("[+] Creando y luego eliminando archivo_temporal.txt en demo/inbox/...")
    tmp_file = inbox / "archivo_temporal.txt"
    tmp_file.write_text("Contenido efimero para probar auditoria de borrado", encoding="utf-8")
    time.sleep(1.0)
    tmp_file.unlink()
    print("  -> Eliminado: archivo_temporal.txt")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "csv":
        test_csv()
    elif mode == "image":
        test_image()
    elif mode == "delete":
        test_delete()
    elif mode == "all":
        test_csv()
        time.sleep(1.5)
        test_image()
        time.sleep(1.5)
        test_delete()
    else:
        print("Modo no reconocido. Opciones: csv, image, delete, all")
