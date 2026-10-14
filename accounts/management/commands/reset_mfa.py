from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from accounts import mfa
from accounts.models import User


class Command(BaseCommand):
    help = (
        'Apaga a verificação em duas etapas de uma conta (celular perdido sem códigos de recuperação, ou SECRET_KEY '
        'trocada). No próximo acesso a pessoa cadastra o aplicativo de novo. Confirme a identidade antes de usar.'
    )

    def add_arguments(self, parser):
        parser.add_argument('conta', help='Usuário ou e-mail da conta.')

    def handle(self, *args, conta, **options):
        users = list(User.objects.filter(Q(username__iexact=conta) | Q(email__iexact=conta)))
        if len(users) != 1:
            raise CommandError('Conta não encontrada.' if not users else 'Mais de uma conta encontrada; use o usuário.')
        user = users[0]
        from accounts.sessions import end_sessions

        removed = mfa.reset(user)
        ended = end_sessions(user)
        if removed:
            self.stdout.write(
                f'Verificação em duas etapas apagada para {user.username}. Sessões abertas encerradas: {ended}.'
            )
        else:
            self.stdout.write(f'{user.username} não tinha verificação em duas etapas cadastrada.')
