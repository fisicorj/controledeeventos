# Arena+

Aplicação institucional para cursos, eventos e agendas, construída com Django, PostgreSQL e Bootstrap.

## Funcionalidades

**Participantes**
- Cadastro, login, logout e recuperação de senha por e-mail
- Catálogo de atividades (só as publicadas e ainda não encerradas)
- Inscrição e cancelamento, com controle de período e de vagas
- Perguntas extras no formulário de inscrição, definidas por atividade (texto, múltipla escolha, sim/não)
- Lista de espera: quando lota, o interessado entra na fila; quando abre vaga, o primeiro é confirmado automaticamente
- Botão de inscrição informa o motivo quando não está disponível (ainda não abriu, encerrada, esgotada)
- "Minhas inscrições" com QR Code individual (o QR contém só um token UUID, nenhum dado pessoal), posição na fila, frequência e certificado
- E-mails: confirmação com o QR anexo, entrada na fila, vaga liberada, cancelamento, lembrete de véspera e certificado disponível
- Certificado em PDF para quem atinge a frequência mínima, com código e QR de validação pública em `/certificados/validar/`
- Emissão avulsa de certificados para eventos que não foram cadastrados no sistema: importa a planilha de participantes (.xlsx ou .csv com Nome, Matrícula, Turma e, opcionalmente, E-mail), mostra uma prévia com os problemas de cada linha, emite e oferece ZIP com todos os PDFs, lista em CSV com códigos e links, e envio por e-mail com o PDF anexo
- Auditoria de certificados (Configurações > Auditoria de certificados, só administradores): emissão, alteração (com antes e depois), revogação, envio por e-mail e download feito pela equipe, com usuário, data e IP. Certificados não são apagados: são revogados com motivo, e a validação pública mostra "Certificado revogado". O registro não pode ser alterado nem apagado, nem direto no banco (trigger no PostgreSQL). Downloads feitos pelo próprio participante e consultas na página pública de validação não são registrados

**Organizadores e administradores**
- Painel do organizador: criar, editar, pré-visualizar, cancelar e excluir atividades
- Atividade com inscrições não é excluída, apenas cancelada (o histórico é preservado)
- Sessões/aulas por atividade: criar, editar e excluir
- Controle de presença por sessão: check-in manual ou por QR Code pela câmera do navegador
- Frequência por inscrito e lista de espera na tela de gestão
- Finalizar atividade: fecha o check-in e emite os certificados de quem atingiu a frequência mínima (padrão 75%, configurável por atividade)
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

Copie `.env.example` para `.env` (`Copy-Item .env.example .env` no Windows, `cp .env.example .env` no Linux/macOS). Os valores padrão já funcionam para desenvolvimento com o banco do Docker.

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
- **Docker**: no cron do servidor, `0 * * * * docker compose -f /caminho/docker-compose.yml exec -T web python manage.py send_reminders`
- **Windows**: no Agendador de Tarefas, uma tarefa de hora em hora executando `venv\Scripts\python.exe manage.py send_reminders` na pasta do projeto

### Testes e padrão de código

```bash
python manage.py test        # usa o PostgreSQL do .env (cria e apaga um banco de teste)
ruff check .                 # aponta problemas
ruff format .                # formata o código
```

O usuário do banco precisa de permissão para criar bancos (`CREATEDB`); o do `docker-compose.yml` já tem.

## Produção

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

Com `DEBUG=False`, a aplicação não sobe sem uma `SECRET_KEY` real. Por padrão, liga cookies seguros e redireciona para HTTPS (`HTTPS_ENABLED`). Atrás de nginx/Caddy, defina `NUM_PROXIES=1`. As demais opções (HSTS, mídia, e-mail SMTP, logs) estão comentadas em `.env.example`.

### Opção 1: Docker

```bash
docker compose --profile prod up -d --build
docker compose exec web python manage.py createsuperuser
```

O container aplica as migrations ao iniciar e roda o gunicorn na porta 8000. O HTTPS fica a cargo de um proxy reverso (nginx, Caddy, Traefik) na frente dele.

### Opção 2: Sem Docker

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
# Linux
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3
# Windows
waitress-serve --listen=0.0.0.0:8000 config.wsgi:application
```

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
