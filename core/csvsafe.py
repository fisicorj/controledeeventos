"""CSV para abrir no Excel sem risco de "injeção de fórmula".

Nomes, respostas e comentários vêm dos participantes. Uma célula que começa com = + - @ (ou tabulação/retorno)
seria interpretada como fórmula pelo Excel/LibreOffice, e poderia, por exemplo, montar um link que envia dados
da planilha para fora. Prefixar um apóstrofo faz a célula ser lida como texto, sem mudar o que aparece.
"""

import csv

DANGEROUS_START = ('=', '+', '-', '@', '\t', '\r')


def safe_cell(value):
    if isinstance(value, str) and len(value) > 1 and value.startswith(DANGEROUS_START):
        return "'" + value
    return value


class SafeWriter:
    def __init__(self, stream, **kwargs):
        self._writer = csv.writer(stream, **kwargs)

    def writerow(self, row):
        return self._writer.writerow([safe_cell(v) for v in row])

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


def excel_writer(stream):
    """Writer com ponto e vírgula (Excel em português) e células protegidas."""
    return SafeWriter(stream, delimiter=';')
