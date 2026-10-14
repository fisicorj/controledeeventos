"""Pequenas normalizações de texto."""


def single_line(value):
    """Junta tudo numa linha só, com um espaço entre as palavras.

    Para títulos e assuntos que vão para o cabeçalho do e-mail: uma quebra de linha ali faz o envio falhar
    (o Django recusa o cabeçalho para impedir injeção), e todas as mensagens daquela atividade ficariam paradas.
    """
    return ' '.join(str(value or '').split())
