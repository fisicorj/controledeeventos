"""Utilidades de requisição HTTP usadas por vários apps."""

from django.conf import settings


def client_ip(request):
    """IP de quem fez a requisição.

    O X-Forwarded-For pode ser forjado pelo cliente. Só é usado quando há proxies confiáveis
    (NUM_PROXIES), pegando o endereço que o proxy mais próximo de nós acrescentou."""
    num_proxies = getattr(settings, 'NUM_PROXIES', 0)
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if num_proxies > 0 and forwarded:
        hops = [h.strip() for h in forwarded.split(',') if h.strip()]
        if len(hops) >= num_proxies:
            return hops[-num_proxies]
    return request.META.get('REMOTE_ADDR')
