# Arena+

Aplicação institucional para cursos, eventos e agendas, construída com Django, PostgreSQL e Bootstrap.

## Funcionalidades

**Participantes**
- Cadastro, login, logout e recuperação de senha por e-mail
- Catálogo de atividades (só as publicadas e ainda não encerradas)
- Inscrição e cancelamento, com controle de período e de vagas
- Perguntas extras no formulário de inscrição, definidas por atividade (texto, múltipla escolha, sim/não)
- Lista de espera: quando lota, o interessado entra na fila; quando abre vaga, o primeiro recebe a vaga reservada e tem um prazo para confirmar (padrão 24 horas, ajustável por atividade; 0 confirma na hora). Sem confirmação no prazo, a vaga passa para o próximo. Perto do início da atividade a confirmação é sempre na hora
- Botão de inscrição informa o motivo quando não está disponível (ainda não abriu, encerrada, esgotada)
- "Minhas inscrições" com QR Code individual (o QR contém só um token UUID, nenhum dado pessoal), posição na fila, frequência e certificado
- E-mails: confirmação com o QR anexo, entrada na fila, vaga liberada, cancelamento, lembrete de véspera e certificado disponível
- Certificado em PDF para quem atinge a frequência mínima, com código e QR de validação pública em `/certificados/validar/`
- "Minhas horas": certificados válidos da pessoa e a soma das horas por semestre, com declaração de horas em PDF (todos os semestres ou um só) e planilha (CSV) para entregar à coordenação. Entram os das atividades em que ela se inscreveu e os de emissões avulsas com o e-mail confirmado da conta ou a matrícula confirmada (o PDF desses também pode ser baixado por ela). Certificado avulso enviado para outro e-mail pode ser vinculado pela própria pessoa com o código, desde que o primeiro e o último nome no certificado sejam os da conta; o vínculo fica na auditoria e pode ser desfeito
- E-mails em texto e em HTML (com a cor, o nome e o logo da instituição); `EMAIL_HTML=False` deixa só o texto
- Emissão avulsa de certificados para eventos que não foram cadastrados no sistema: importa a planilha de participantes (.xlsx ou .csv com Nome, Matrícula, Turma e, opcionalmente, E-mail) com os dados do evento digitados na tela, ou uma planilha completa que já traz também os dados do evento (Evento, Tipo, Início, Término, Carga horária, Data do certificado; um evento por planilha, com modelo para baixar), mostra uma prévia com os problemas de cada linha, emite e oferece ZIP com todos os PDFs, lista em CSV com códigos e links, e envio por e-mail com o PDF anexo
- Auditoria de certificados (Configurações > Auditoria de certificados, só administradores): emissão, alteração (com antes e depois), revogação, envio por e-mail e download feito pela equipe, com usuário, data e IP. Certificados não são apagados: são revogados com motivo, e a validação pública mostra "Certificado revogado". O registro não pode ser alterado nem apagado, nem direto no banco (trigger no PostgreSQL). Downloads feitos pelo próprio participante e consultas na página pública de validação não são registrados
- Página do evento com endereço amigável (`/eventos/nome-do-evento/`), palestrantes com foto e minibio, programação com quem fala em cada sessão, link do mapa, botões "Adicionar à agenda" (Google, Outlook e arquivo .ics, que também vai anexo na confirmação da inscrição) e compartilhamento no WhatsApp e LinkedIn com prévia (Open Graph)
- Pesquisa de satisfação: ao finalizar a atividade, quem participou recebe o convite para dar nota de 0 a 10 e comentar. A organização vê média, NPS, distribuição das notas e comentários, sem o nome de quem respondeu (os comentários e o CSV saem em ordem de nota e só com o dia, para a hora da resposta não identificar ninguém). Opcionalmente, o certificado só é liberado depois da resposta
- Comunicados: o organizador envia avisos por e-mail aos confirmados e/ou à lista de espera (as respostas vão para o e-mail dele), e o aviso aparece na página da atividade para os inscritos
- Tipos de vaga: divide as vagas (ex.: alunos e público externo, ou reservadas por turma), cada tipo com vagas e lista de espera próprias, código de acesso opcional e opção "só para alunos com matrícula confirmada". A atividade também pode ter um código de acesso geral (evento restrito)
- Duplicar atividade para a próxima edição: copia sessões, palestrantes, perguntas, tipos de vaga e configurações, deslocando todas as datas; as edições ficam ligadas
- Números da atividade: inscrições e desistências por dia, ocupação, comparecimento, frequência média, origem por tipo de vaga, matrícula e perguntas de escolha, e comparação entre edições
- Check-in no celular como aplicativo (PWA, em `/attendance/`): instala na tela inicial, funciona sem internet guardando as leituras no aparelho e envia quando a conexão volta, com contador ao vivo de presentes (inclui leituras de outros celulares). A câmera do celular exige HTTPS
- Crachás em PDF com nome, tipo de vaga e o QR da inscrição, em três formatos de folha (cartões para cortar, etiquetas A4 e etiquetas Carta), com ajuste fino de margem e opção de pular etiquetas já usadas
- Materiais pós-evento: arquivos (até 50 MB) e links liberados só para quem teve presença (ou para todos os inscritos), a partir do fim da atividade ou de uma data escolhida. Os arquivos ficam em `private_media/` (variável `PRIVATE_MEDIA_ROOT`), fora do `/media/` público: inclua essa pasta no backup
- Cadastro com confirmação de e-mail por código de 6 dígitos (vale 15 minutos, até 5 tentativas). A troca de e-mail em "Meus dados" também pede código. Sem confirmar, a conta não se inscreve, e cadastros não confirmados em 7 dias são apagados
- O cadastro e a troca de e-mail não revelam se um endereço já tem conta: a tela é sempre "enviamos um código". Se o e-mail já pertence a uma conta confirmada, o código não sai e o dono recebe um aviso com o usuário dele e o link de nova senha (no máximo 3 avisos por dia)
- Campo "Matrícula ou CPF" no cadastro: o aluno digita a matrícula, que precisa ser a mesma do e-mail institucional (Configurações > "Domínio do e-mail dos alunos", ex.: `aluno.fsa.br`) e só vale como matrícula de aluno depois que o código for confirmado. Convidados podem informar o CPF (dígitos conferidos). Uma conta que nunca confirmou o e-mail não segura o endereço: quem confirmar o código (ou redefinir a senha pelo link) fica com ele
- Privacidade (LGPD): aviso de privacidade público (`/privacidade/`), aceite no cadastro, cópia dos dados em JSON e exclusão da conta pelo próprio participante em "Meus dados" (anonimiza o cadastro, cancela inscrições futuras e mantém certificados já emitidos válidos). Na validação pública o CPF aparece mascarado. O contato para assuntos de privacidade fica em Configurações. Destinatário e conteúdo dos e-mails enviados são apagados depois de 30 dias (fica só a contagem dos envios), e na exclusão da conta na hora
- Inscrição confirmada não pode ser cancelada depois que a atividade começa (a presença e o certificado dependem dela); sair da lista de espera continua liberado
- Limite de tentativas (guardado no banco, vale para todos os processos): login do portal e do /admin/ (em 15 minutos: 5 erros para o mesmo usuário vindo do mesmo IP, 50 para o mesmo usuário de qualquer lugar, 100 por IP; quem erra a senha de outra pessoa a partir de casa não impede o dono de entrar), recuperação de senha (3 por e-mail e 30 por IP por hora), envio de código (3 por pessoa e 60 por IP por hora; os limites por IP são altos porque um laboratório inteiro sai pelo mesmo IP), código de acesso de evento (10 erros por pessoa por hora) e validação de certificado (30 códigos inexistentes por IP por hora)

**Organizadores e administradores**
- Painel do organizador: criar, editar, pré-visualizar, cancelar e excluir atividades, com busca, filtro por semestre e por situação e 20 atividades por página (catálogo, emissões avulsas e certificados de um lote também são paginados, e o lote tem busca por nome, matrícula ou turma)
- Atividade com inscrições não é excluída, apenas cancelada (o histórico é preservado)
- Sessões/aulas por atividade: criar, editar e excluir
- Controle de presença por sessão: check-in manual ou por QR Code pela câmera do navegador
- Frequência por inscrito e lista de espera na tela de gestão
- Finalizar atividade: fecha o check-in e emite os certificados de quem atingiu a frequência mínima (padrão 75%, configurável por atividade)
- Equipe de check-in por atividade: monitores e voluntários (com conta de participante) registram presença, abrem e fecham o check-in, sem ver e-mails, relatórios ou configurações
- Mudou a data ou o local de uma atividade com inscritos: os inscritos podem ser avisados por e-mail com o antes e o depois, e o lembrete de véspera vale para a data nova
- Certificado emitido pode ser corrigido (nome ou matrícula, mantendo o código) ou revogado, com motivo e o antes e depois na auditoria; presença lançada por engano pode ser desfeita, e a remoção fica num registro permanente (quem removeu, quando, de qual IP e quem tinha registrado), visível para a organização na tela de presença da sessão e protegido contra alteração no banco
- Verificação em duas etapas para administradores e organizadores: código de 6 dígitos de um aplicativo autenticador (Google Authenticator, Microsoft Authenticator, 2FAS etc.) depois da senha, em todo login (inclusive no /admin/), com 10 códigos de recuperação de uso único. A sessão dessas contas dura no máximo 8 horas
- Fila de e-mails: avisos em massa (finalização, comunicados, certificados avulsos) saem em segundo plano, com novas tentativas automáticas se o servidor de e-mail falhar; o andamento aparece nas telas e em Configurações > E-mail
- Relatórios: inscritos com respostas e frequência em CSV (abre direto no Excel), frequência consolidada em PDF e lista de assinatura por sessão em PDF
- Django Admin para gestão geral

## Requisitos

- Python 3.12+
- PostgreSQL 16/17 (ou Docker Desktop para subir o banco)

## Desenvolvimento

### 1. Ambiente Python

```powershell
# Windows (PowerShell)
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```

```bash
# Linux/macOS
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

### 2. Variáveis de ambiente

Copie `.env.example` para `.env` (`Copy-Item .env.example .env` no Windows, `cp .env.example .env` no Linux/macOS) e, para desenvolvimento, mude `DEBUG=True`. Com isso os demais valores padrão já funcionam com o banco do Docker. A chave de exemplo só é aceita com `DEBUG=True` e acesso pela própria máquina; em qualquer servidor, gere uma `SECRET_KEY` própria.

### 3. Banco de dados

```bash
docker compose up -d db
```

Se preferir um PostgreSQL já instalado, crie o banco `arena_events` e ajuste o `.env`.

### 4. Preparar e rodar

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- Aplicação: http://127.0.0.1:8000/
- Administração: http://127.0.0.1:8000/admin/

As migrations já fazem parte do projeto. **Não é preciso rodar `makemigrations`**, a não ser que você altere algum model.

### Primeiro teste

1. Entre em `/admin/` com o superusuário.
2. Em Usuários, defina o seu papel como `Administrador` (ou crie um usuário `Organizador`).
3. No "Painel do organizador", crie uma atividade com status `Publicado`, período de inscrição incluindo a data atual e capacidade maior que zero.
4. Em "Gerenciar", crie uma sessão e marque "check-in aberto".
5. Em uma janela anônima, crie uma conta de participante, inscreva-se e abra "Minhas inscrições" para ver o QR Code.
6. De volta ao organizador, abra a presença da sessão e leia o QR Code pela câmera.
7. Em "Gerenciar", clique em "Finalizar atividade": quem tiver a frequência mínima recebe o certificado por e-mail e pode baixá-lo em "Minhas inscrições".

A câmera exige HTTPS, exceto em `localhost`. Para testar com o celular na rede local, use um túnel HTTPS ou o deploy com proxy (veja Produção).

### E-mails em desenvolvimento

Enquanto o envio estiver desligado em **Configurações > E-mail** (o padrão), as mensagens (confirmações, recuperação de senha, certificados) aparecem no terminal do `runserver`.

### Lembretes de véspera

O lembrete é enviado por um comando que deve rodar de hora em hora. Cada inscrição recebe um único lembrete, quando faltam até 24 horas para a atividade começar.

```bash
python manage.py send_reminders --simular   # mostra quem receberia, sem enviar
python manage.py send_reminders             # envia
```

Para agendar:

- **Linux (cron)**: `0 * * * * cd /caminho/arena-events && venv/bin/python manage.py send_reminders`
- **Docker**: o serviço `worker` já envia os lembretes de hora em hora; não precisa de cron. (Se preferir o cron do servidor, rode o worker com `--sem-lembretes` para não haver dois envios ao mesmo tempo.)
- **Windows**: no Agendador de Tarefas, uma tarefa de hora em hora executando `venv\Scripts\python.exe manage.py send_reminders` na pasta do projeto

### Prazo da lista de espera

Vagas oferecidas e não confirmadas no prazo voltam para a fila sozinhas sempre que alguém se inscreve ou cancela naquela atividade. Para não depender disso, o serviço `worker` do Docker confere a cada 5 minutos. Sem Docker, agende junto com os lembretes: `python manage.py expire_waitlist_offers` (de hora em hora, ou com mais frequência).

### Testes e padrão de código

```bash
python manage.py test        # usa o PostgreSQL do .env (cria e apaga um banco de teste)
ruff check .                 # aponta problemas (inclui as regras de segurança "S", equivalentes ao bandit)
coverage run manage.py test && coverage report   # cobertura dos testes (a CI mostra o relatório)
ruff format .                # formata o código
```

O usuário do banco precisa de permissão para criar bancos (`CREATEDB`); o do `docker-compose.yml` já tem.

## Produção

### Antes de abrir para usuários reais

Confira item por item (os detalhes estão nas seções abaixo):

- [ ] `SECRET_KEY` própria, com cópia guardada fora do servidor (a senha do SMTP e a verificação em duas etapas dependem dela)
- [ ] `DEBUG=False`; `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` e `SITE_URL` com o endereço público
- [ ] Senha do PostgreSQL trocada (no `docker-compose.yml` e no `.env`)
- [ ] Banco com dois papéis: um dono das tabelas, só para migrações e backup, e outro para a aplicação (veja "Banco: dois papéis")
- [ ] Proxy reverso com HTTPS, `NUM_PROXIES=1` e **limite de tamanho de envio** (veja "Proxy reverso")
- [ ] HSTS ligado com valor baixo (`SECURE_HSTS_SECONDS=3600`), aumentando depois que o HTTPS estiver estável
- [ ] `ADMIN_URL` próprio (o painel sai de `/admin/`) e, se possível, restrito por IP no proxy
- [ ] `python manage.py check --deploy` sem avisos que você não tenha decidido aceitar
- [ ] Serviço `worker` rodando (ou, sem Docker, os comandos agendados: `process_outbox`, `send_reminders`, `expire_waitlist_offers`, `run_maintenance`)
- [ ] Servidor de e-mail configurado em Configurações > E-mail, com teste enviado, e e-mail para alertas de erro
- [ ] Backup agendado, cópia para fora do servidor e um teste de restauração feito
- [ ] `AUDIT_EXPORT_DIR` definido, se a auditoria de certificados tiver valor legal para a instituição
- [ ] Primeiro administrador criado e com a verificação em duas etapas ativada

### Variáveis obrigatórias no `.env`

```env
DEBUG=False
SECRET_KEY=<gere uma chave>
ALLOWED_HOSTS=eventos.exemplo.br
CSRF_TRUSTED_ORIGINS=https://eventos.exemplo.br
```

Para gerar a chave:

```bash
python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
```

Com `DEBUG=False`, a aplicação não sobe sem uma `SECRET_KEY` real, com pelo menos 50 caracteres (o comando do `.env.example` gera uma assim). Por padrão, liga cookies seguros e redireciona para HTTPS (`HTTPS_ENABLED`). Atrás de nginx/Caddy, defina `NUM_PROXIES=1`. As demais opções (HSTS, mídia, e-mail SMTP, logs) estão comentadas em `.env.example`.

### Opção 1: Docker

```bash
docker compose --profile prod up -d --build
docker compose exec web python manage.py createsuperuser
```

O container aplica as migrations ao iniciar e roda o gunicorn na porta 8000. O serviço `worker` envia a fila de e-mails (veja "Fila de e-mails"). O HTTPS fica a cargo de um proxy reverso (nginx, Caddy, Traefik) na frente dele.

### Opção 2: Sem Docker

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
# Linux
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
# Windows
waitress-serve --listen=0.0.0.0:8000 config.wsgi:application
```

### Proxy reverso: HTTPS e limite de envio

A aplicação roda em HTTP na porta 8000; o HTTPS e o limite de tamanho das requisições ficam no proxy. O limite é importante: o Django confere o tamanho dos arquivos (capa até 5 MB, planilha até 5 MB, material até 50 MB) só **depois** de receber tudo, então sem limite no proxy alguém com acesso de organizador poderia enviar arquivos de vários GB e encher o disco temporário. Use um pouco acima do maior arquivo aceito:

```nginx
# nginx
server {
    server_name eventos.exemplo.br;
    client_max_body_size 60m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    # listen 443 ssl; ssl_certificate ...; (ou use o certbot)
}
```

```caddyfile
# Caddy (HTTPS automático)
eventos.exemplo.br {
    request_body {
        max_size 60MB
    }
    reverse_proxy 127.0.0.1:8000
}
```

Com um proxy na frente, defina `NUM_PROXIES=1` no `.env` (o IP real de quem acessa vem do `X-Forwarded-For`, usado nos limites de tentativas e na auditoria).

### Banco: dois papéis

A auditoria de certificados e o registro de presenças removidas são protegidos por triggers que recusam alteração e exclusão. Só que o dono de uma tabela pode apagar o trigger dela. Por isso, em produção, a aplicação deve usar um papel que **não** é dono de nada:

- `DB_MIGRATE_USER` / `DB_MIGRATE_PASSWORD`: dono das tabelas. Usado só por `migrate`, `setup_db_roles` e pelos scripts de backup.
- `DB_USER` / `DB_PASSWORD`: papel da aplicação. Lê e grava os dados, mas não muda a estrutura do banco, não altera nem apaga a auditoria e não mexe na tabela de migrações.

O comando `python manage.py setup_db_roles` (rodado com o dono) cria o papel da aplicação, ou atualiza a senha e as permissões dele, e faz as tabelas criadas por migrações futuras já nascerem com as mesmas permissões. Pode rodar quantas vezes quiser. O container `web` já roda o comando depois do `migrate` sempre que `DB_MIGRATE_USER` estiver definido.

**Instalação nova com Docker:** no `.env`, defina os dois papéis com nomes diferentes:

```env
DB_MIGRATE_USER=arena_owner
DB_MIGRATE_PASSWORD=<senha forte>
DB_USER=arena_app
DB_PASSWORD=<outra senha forte>
```

O PostgreSQL do `docker-compose.yml` cria o `DB_MIGRATE_USER` na primeira subida, e o `web` cria o `arena_app`.

**Banco que já existe (um usuário só até agora):** o usuário atual vira o dono, e você cria um novo para a aplicação. No `.env`, passe o usuário e a senha atuais para `DB_MIGRATE_USER` e `DB_MIGRATE_PASSWORD`, e coloque em `DB_USER` e `DB_PASSWORD` um nome e uma senha novos. Depois, `docker compose --profile prod up -d` (ou, sem Docker, `python manage.py migrate` seguido de `python manage.py setup_db_roles`).

Sem Docker, o dono precisa poder criar papéis (`CREATEROLE`). Se não puder, crie o papel da aplicação uma vez como superusuário (`CREATE ROLE arena_app LOGIN PASSWORD '...';`) e rode `setup_db_roles`, que aplica as permissões.

Sem `DB_MIGRATE_USER`, um único usuário faz tudo, como no desenvolvimento e nos testes (que precisam de um usuário com permissão para criar o banco de teste).

### Regras de data do banco

As regras que impedem datas trocadas (fim antes do início em atividades, sessões e certificados) foram criadas sem conferir os registros antigos, para não travar o deploy. Para conferir tudo e deixá-las valendo também para o que já existia:

```bash
python manage.py validate_constraints --conferir   # só lista o que falta
python manage.py validate_constraints              # valida as regras sem pendências
```

Se algum registro antigo violar uma regra, o comando mostra os ids; corrija pelo `/admin/` e rode de novo. Com `DB_MIGRATE_USER` configurado, o comando usa o dono das tabelas automaticamente (no Docker: `docker compose exec web python manage.py validate_constraints`).

### Verificação em duas etapas

Em produção (`DEBUG=False`), administradores, organizadores e quem acessa o `/admin/` precisam cadastrar um aplicativo autenticador no primeiro acesso: o sistema mostra um QR Code, pede um código para confirmar e entrega 10 códigos de recuperação (aparecem uma vez só; guarde-os). Depois disso, todo login pede o código do aplicativo. Em **Meus dados > Verificação em duas etapas** dá para gerar novos códigos de recuperação ou trocar de celular.

- Celular perdido e sem códigos de recuperação: depois de confirmar a identidade da pessoa, um administrador do servidor executa `python manage.py reset_mfa <usuário ou e-mail>` (no Docker: `docker compose exec web python manage.py reset_mfa <usuário>`). No próximo acesso ela cadastra de novo.
- O segredo de cada cadastro fica criptografado com uma chave derivada da `SECRET_KEY`. Se a `SECRET_KEY` mudar, todos precisam cadastrar de novo (use `reset_mfa` para cada conta).
- `MFA_REQUIRED=False` deixa a verificação opcional (só quem ativar em "Meus dados"). Com `DEBUG=True` ela já é opcional, para não atrapalhar o desenvolvimento.
- `PRIVILEGED_SESSION_HOURS` (padrão 8) é a duração máxima da sessão dessas contas; participantes continuam com 2 semanas.
- `reset_mfa` também encerra todas as sessões abertas da conta: quem estava com o celular perdido (ou com a sessão roubada) precisa entrar de novo.
- O código também é limitado por conta: além de 5 tentativas erradas por endereço IP, mais de 20 erros em 15 minutos bloqueiam a conta inteira por esse tempo, venham de onde vierem.

**Confirmação de identidade.** Ações sensíveis pedem a senha de novo (e o código do aplicativo, se a conta usa verificação em duas etapas) quando o último login ou confirmação foi há mais de `REAUTH_MINUTES` minutos (padrão 15): revogar certificado, cancelar lote ou revogar certificados de um lote e as telas de **Configurações > E-mail** (inclusive o envio de teste). Assim uma sessão esquecida aberta não basta para essas ações.

### Segurança do navegador (CSP)

Toda página sai com `Content-Security-Policy`: só scripts do próprio portal (pasta `static/js/`; o Bootstrap também fica no portal, em `static/vendor/bootstrap/`, sem depender de outro site), nada de script dentro do HTML, e o portal não pode ser aberto dentro de outro site. A câmera só é liberada (`Permissions-Policy`) nas telas de leitura de QR. Se algo for bloqueado, o navegador avisa o portal e o aviso aparece no log (`arena.csp`).

- `CSP_MODE=report` só registra no log, sem bloquear (útil para investigar um problema); `CSP_MODE=off` desliga.
- Ao mudar templates, não use `<script>` com código nem `onclick=""`: coloque o código em `static/js/app.js` (ou num arquivo novo) e ligue pelo atributo `data-*`. O teste `core.test_security_headers` confere isso nas principais telas.

Ao sair do portal, o navegador apaga os dados do check-in guardados no aparelho (lista de inscritos e telas offline). Se ainda houver leituras sem enviar, o portal pergunta antes.

### Painel administrativo, banco remoto e outros avisos

- `ADMIN_URL` (ex.: `ADMIN_URL=gestao-arena/`) troca o endereço do painel `/admin/`. Se possível, restrinja esse caminho por IP no proxy.
- Banco em outro servidor: `DB_SSLMODE=require` (ou `verify-full`) criptografa a conexão.
- `python manage.py check --deploy` mostra também os avisos do Arena+ (`arena.W001` a `W003`): `SERVE_MEDIA` ligado em produção, banco remoto sem criptografia e painel no endereço padrão.
- A tela **Configurações > E-mail > Enviar teste** faz o servidor conectar no host e porta informados: deixe-a só com quem administra o servidor.
- A fila de e-mails entrega "pelo menos uma vez": se o processo for derrubado no instante entre o servidor de e-mail aceitar a mensagem e o portal anotar o envio, ela sai de novo depois de 10 minutos. É raro e inofensivo (um aviso repetido).

### Exportação da auditoria

`python manage.py export_audit --pasta <pasta>` grava a auditoria de certificados e as presenças removidas em arquivos JSON Lines encadeados por hash: cada linha leva o hash da anterior, então alterar, apagar ou reordenar qualquer linha quebra a cadeia. Cada exportação continua de onde a anterior parou. `--conferir` verifica todos os arquivos da pasta.

Com `AUDIT_EXPORT_DIR` no `.env`, o serviço `worker` exporta uma vez por dia. Duas exportações ao mesmo tempo (o worker e um comando manual) não acontecem: a segunda é recusada com aviso. Quando sai algo novo, o portal manda por e-mail o último registro e o último hash de cada tipo, para `AUDIT_EXPORT_EMAIL` ou, sem ele, para o e-mail de alertas de erro. Guarde essas mensagens: ficam fora do servidor e mostram depois se alguém refez a cadeia inteira (o hash final mudaria). No Docker, use uma pasta dentro do volume de arquivos privados, que já entra no backup: `AUDIT_EXPORT_DIR=/app/private_media/auditoria-exportada`. Para valer como prova, copie a pasta para um armazenamento que não permita alterar nem apagar (por exemplo, um bucket com bloqueio de objetos) e fora do servidor.

### Manutenção e saúde

O serviço `worker` do Docker, além da fila de e-mails, faz sozinho:

- a cada 5 minutos: devolve à fila de espera as vagas não confirmadas no prazo e apaga o conteúdo dos e-mails antigos;
- de hora em hora: lembretes de véspera;
- uma vez por dia: apaga sessões de login vencidas, códigos de confirmação com mais de 7 dias, contadores de tentativas antigos, cadastros de participante não confirmados em 7 dias e cópias de imagens do certificado sem uso há 30 dias; e exporta a auditoria, se `AUDIT_EXPORT_DIR` estiver definido.

Sem Docker, agende `python manage.py run_maintenance` uma vez por dia (além de `process_outbox`, `send_reminders` e `expire_waitlist_offers`, já descritos).

Em **Configurações > Manutenção** (só administradores) aparecem o resultado da última faxina, tarefa por tarefa, as execuções recentes e a situação da exportação da auditoria (arquivos, último registro e hash, quanto falta exportar). A tela avisa quando a faxina não roda há mais de um dia e meio (worker parado ou agendamento esquecido) e tem o botão "Rodar agora", que pede a senha de novo se o login for antigo.

A rota `/saude/` responde `{"situacao": "ok", "banco": "ok"}` (código 200) quando a aplicação e o banco estão funcionando, e 503 quando não. Ela atende por `http://127.0.0.1:8000/saude/` mesmo fora do `ALLOWED_HOSTS` e sem HTTPS, para o `healthcheck` do Docker (o `web` aparece como `healthy` em `docker compose ps`, e o `worker` só sobe depois dele) e para um monitor externo, como o UptimeRobot.

As datas das atividades, sessões e emissões avulsas (término depois do início, inscrições até o término) também são conferidas pelo banco. Ao atualizar, se algum registro antigo tiver datas invertidas, o `migrate` lista quais são para corrigir pelo /admin/; o sistema continua funcionando.

### Imagens enviadas (`/media/`)

O ideal é o proxy reverso servir a pasta `media/`. Em instalações pequenas, `SERVE_MEDIA=True` faz o próprio Django servir.

### E-mail

O servidor de envio é configurado dentro da plataforma, em **Configurações > E-mail** (visível para administradores):

- servidor SMTP ou relay, porta e segurança (STARTTLS, SSL/TLS ou nenhuma, para relay interno na porta 25);
- usuário e senha, opcionais (em branco para relay liberado pelo IP do servidor); a senha fica criptografada no banco;
- opção de não verificar o certificado, para relay interno com certificado autoassinado;
- e-mail e nome do remetente, "responder para" e tempo limite;
- botão **Enviar teste**, que mostra o erro do servidor quando algo falha.

Enquanto o envio estiver desligado nessa tela, os e-mails não saem: aparecem só no log/terminal.

A senha do SMTP é criptografada com uma chave derivada da `SECRET_KEY`. Se a `SECRET_KEY` mudar, cadastre a senha de novo.

Defina também `SITE_URL` no `.env` (endereço público, usado nos links dos e-mails e no QR de validação dos certificados).

### Alerta de erros

Em **Configurações > E-mail**, o campo "E-mail para alertas de erro" recebe um aviso quando o portal dá erro 500, com o endereço da página e o detalhe técnico (no máximo 10 por hora).

### Dependências

As versões ficam fixas em `requirements.txt`: o servidor recebe exatamente o que foi testado. O Dependabot (`.github/dependabot.yml`) abre um pull request quando sai versão nova (pacotes Python, actions da CI e imagem base do Docker), e a CI roda os testes nele. Para atualizar à mão, troque a versão, rode `python manage.py test` e faça o deploy.

A CI só tem permissão de leitura no repositório, e as actions ficam fixas pelo hash do commit (o comentário ao lado mostra a versão), para que uma tag alterada por terceiros não rode código diferente. Para fixar também a imagem base do Docker, veja o comentário no início do `Dockerfile`: `docker pull python:3.12-slim` e depois `docker inspect --format '{{index .RepoDigests 0}}' python:3.12-slim` mostram o digest para colocar no `FROM`.

A CI também roda o `pip-audit`, que falha quando alguma versão fixada tem vulnerabilidade conhecida publicada (base do PyPI/OSV), e as regras de segurança do `ruff`. Para conferir na sua máquina: `pip-audit -r requirements.txt -r requirements-dev.txt`.

### Fila de e-mails

Os e-mails das ações do sistema (confirmações, finalização com certificados, comunicados, certificados avulsos) entram numa fila no banco e saem em segundo plano, um por um. A tela responde na hora, mesmo com centenas de participantes, e quem já recebeu não recebe de novo. Se o servidor de e-mail falhar, o sistema tenta de novo sozinho (1, 5, 15 e 60 minutos depois); depois de 5 tentativas a mensagem aparece como "falhou" em **Configurações > E-mail**, com o botão para tentar de novo. O botão só reenvia as falhas dos últimos 3 dias (`OUTBOX_RETRY_DAYS`): um aviso de vaga, um lembrete ou um código de semanas atrás chegaria fora de hora.

Por padrão (`OUTBOX_MODE=thread`) o próprio servidor envia, logo depois de cada operação. Como rede de segurança (ex.: o servidor reiniciou no meio de um lote), deixe também o comando abaixo rodando:

- **Docker**: o serviço `worker` do `docker-compose.yml` já faz isso (`python manage.py process_outbox --continuo`);
- **Linux (cron)**: `* * * * * cd /caminho/arena-events && venv/bin/python manage.py process_outbox`
- **Windows**: no Agendador de Tarefas, uma tarefa de minuto em minuto executando `venv\Scripts\python.exe manage.py process_outbox` na pasta do projeto.

No Docker, o serviço `web` já sobe com `OUTBOX_MODE=off` (definido no `docker-compose.yml`, vale mais que o `.env`): ele só coloca as mensagens na fila e o `worker` envia. Assim um reinício do servidor web no meio de um lote não atrasa nada. Sem Docker, use `OUTBOX_MODE=off` quando o `process_outbox` estiver agendado de minuto em minuto. Vários processos podem trabalhar ao mesmo tempo sem duplicar mensagens.

**ZIP de lotes grandes.** Na emissão avulsa, lotes com até 200 certificados baixam o ZIP na hora. Acima disso, o botão vira "Gerar ZIP de todos": o arquivo é montado em segundo plano (pelo `worker`, ou pelo próprio servidor com `OUTBOX_MODE=thread`), a página se atualiza sozinha e mostra o link quando fica pronto. O arquivo fica em `private_media/zips/` por 24 horas e é descartado se o lote mudar (participantes novos, revogação ou edição dos dados).

**Retenção.** Depois de `OUTBOX_RETENTION_DAYS` dias (padrão 30), destinatário, assunto e conteúdo dos e-mails enviados, e dos que falharam de vez, são apagados; fica só a contagem, que alimenta o andamento dos envios nas telas. O serviço `worker` faz isso sozinho. Sem ele, agende uma vez por dia `python manage.py purge_outbox`. Na exclusão de conta, as mensagens da pessoa são limpas na hora.

### Backup e restauração

Os scripts da pasta `scripts/` fazem o backup do banco (`pg_dump`) e das pastas `media/` e `private_media/` (imagens, assinatura, arte do certificado e materiais), conferem se os arquivos abrem e apagam os mais antigos que `KEEP_DAYS` (padrão 14 dias):

```bash
bash scripts/backup.sh                # com Docker (serviços db e web rodando)
bash scripts/backup.sh --sem-docker   # sem Docker (usa pg_dump e os dados do .env)
```

Agende uma vez por dia (cron: `30 2 * * * cd /opt/arena-events && bash scripts/backup.sh >> backups/backup.log 2>&1`) e **copie a pasta `backups/` para fora do servidor** (outro disco, outro servidor ou nuvem): backup no mesmo disco não protege contra a perda do servidor. O backup tem dados pessoais; guarde com acesso restrito.

**Criptografia (recomendado quando o backup sai do servidor).** Instale o [age](https://github.com/FiloSottile/age) no servidor, gere um par de chaves num computador seguro (`age-keygen -o chave-backup.txt`; a linha "public key" é a chave pública) e coloque no `.env` do servidor só a chave pública: `BACKUP_AGE_RECIPIENT=age1...` (ou o caminho de um arquivo com várias chaves públicas). O backup passa a gerar arquivos `.age` e apaga as versões abertas; quem rouba a cópia não lê nada sem a chave privada. A chave privada **não** fica no servidor: guarde-a fora (cofre de senhas, pendrive guardado) e, na hora de restaurar, aponte `BACKUP_AGE_IDENTITY=/caminho/chave-backup.txt` e passe os arquivos `.age` ao `restore.sh`, que decifra numa pasta temporária e apaga ao terminar. Perder a chave privada é perder os backups: teste a restauração logo depois de configurar.

Teste a restauração uma vez por mês. O modo de teste restaura num banco temporário, mostra quantas linhas têm as tabelas principais e apaga o banco, sem mexer no que está em uso:

```bash
bash scripts/restore.sh --teste backups/arena-db-AAAAMMDD-HHMMSS.dump backups/arena-arquivos-AAAAMMDD-HHMMSS.tar.gz
```

Para restaurar de verdade (substitui banco e arquivos), pare o site, rode sem `--teste` e confirme digitando `RESTAURAR`:

```bash
docker compose stop web worker
bash scripts/restore.sh backups/arena-db-AAAAMMDD-HHMMSS.dump backups/arena-arquivos-AAAAMMDD-HHMMSS.tar.gz
docker compose --profile prod up -d
```

Guarde também o `.env` (principalmente a `SECRET_KEY`) num lugar seguro e separado: sem a mesma chave, a senha do SMTP e os cadastros da verificação em duas etapas precisam ser refeitos.

### Configurações da instituição

Nome da instituição, cidade, logo, nome e cargo de quem assina e imagem da assinatura dos certificados são configurados dentro da plataforma, no menu **Configurações** (visível para superusuários e usuários com papel `Administrador`). A mesma tela tem um botão para pré-visualizar o certificado.

Se o servidor de e-mail falhar, a operação do usuário (inscrição, cancelamento etc.) continua funcionando e o erro fica registrado no log.

### Leitor de QR Code

A biblioteca `html5-qrcode` (2.3.8) fica em `static/vendor/`, então o scanner funciona mesmo sem acesso à internet.

## Histórico

- **V0.5**: certificados com validação pública, perguntas personalizadas na inscrição, lista de espera com promoção automática, e-mails aos participantes (confirmação, fila, vaga, cancelamento, lembrete, certificado) e relatórios em CSV e PDF.
- **V0.4**: correções de segurança e integridade, validações de datas e capacidade, cancelamento de atividade, edição de sessões, configuração de produção (WhiteNoise, gunicorn/waitress, Docker), recuperação de senha, pré-visualização de rascunhos, scanner sem recarregar a página e suíte de testes.
- **V0.3**: painel do organizador, sessões, frequência, check-in manual e por QR Code.
- **V0.2**: cadastro, catálogo, inscrições, vagas e QR Code individual.
