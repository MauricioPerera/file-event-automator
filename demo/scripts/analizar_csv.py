import sys
import csv
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("Uso: python analizar_csv.py <ruta_csv>")
        sys.exit(1)

    filepath = Path(sys.argv[1])
    if not filepath.exists():
        print(f"Error: No existe el archivo {filepath}")
        sys.exit(1)

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader, None)
        rows = list(reader)

    print("=" * 55)
    print(f" [ETL ANALISIS] Archivo: {filepath.name}")
    print(f"   * Columnas ({len(headers) if headers else 0}): {headers}")
    print(f"   * Filas de datos: {len(rows)}")
    print(f"   * Tamano: {filepath.stat().st_size} bytes")
    print("=" * 55)

if __name__ == "__main__":
    main()
