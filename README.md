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
- Certificado em PDF para quem atinge a frequência mínima, com código e QR de validação pública em `/certificados/validar/`; instituições parceiras e coordenações conferem até 50 códigos de uma vez (certificados e declarações de horas) em `/certificados/validar-varios/`
- "Minhas horas": certificados válidos da pessoa e a soma das horas por semestre, com declaração de horas em PDF (todos os semestres ou um só; cada declaração tem código e QR para uma página pública que mostra a lista oficial e se cada certificado continua válido, então um PDF editado é descoberto; baixar de novo no mesmo dia, com a mesma lista, repete o código, e a pessoa vê as declarações que emitiu e pode cancelar uma, quando a página pública deixa de mostrar os dados) e planilha (CSV) para entregar à coordenação. Entram os das atividades em que ela se inscreveu e os de emissões avulsas com o e-mail confirmado da conta ou a matrícula confirmada (o PDF desses também pode ser baixado por ela). Certificado avulso enviado para outro e-mail pode ser vinculado pela própria pessoa com o código, desde que o primeiro e o último nome no certificado sejam os da conta, o CPF (se a planilha trouxer) seja o mesmo da conta e o certificado não pertença a outra conta. O vínculo fica na auditoria, aparece marcado na declaração, o e-mail da planilha é avisado e a pessoa pode desfazer
- E-mails em texto e em HTML (com a cor, o nome e o logo da instituição); `EMAIL_HTML=False` deixa só o texto
- Emissão avulsa de certificados para eventos que não foram cadastrados no sistema: importa a planilha de participantes (.xlsx ou .csv com Nome, Matrícula, Turma e, opcionalmente, E-mail) com os dados do evento digitados na tela, ou uma planilha completa que já traz também os dados do evento (Evento, Tipo, Início, Término, Carga horária, Data do certificado; um evento por planilha, com modelo para baixar), mostra uma prévia com os problemas de cada linha, emite e oferece ZIP com todos os PDFs, lista em CSV com códigos e links, e envio por e-mail com o PDF anexo. "Horas por turma" soma, para cada participante de uma turma (coluna "Turma" da planilha), as horas dos certificados válidos, com filtro por semestre e CSV para a coordenação
- Auditoria de certificados (Configurações > Auditoria de certificados, só administradores): emissão, alteração (com antes e depois), revogação, envio por e-mail e download feito pela equipe, com usuário, data e IP. Certificados não são apagados: são revogados com motivo, e a validação pública mostra "Certificado revogado". O registro não pode ser alterado nem apagado, nem direto no banco (trigger no PostgreSQL). Downloads feitos pelo próprio participante e consultas na página pública de validação não são registrados
- Página do evento com endereço amigável (`/eventos/nome-do-evento/`), palestrantes com foto e minibio, programação com quem fala em cada sessão, link do mapa, botões "Adicionar à agenda" (Google, Outlook e arquivo .ics, que também vai anexo na confirmação da inscrição; na atividade por sessão, o .ics da pessoa leva só as sessões em que ela está confirmada, e "Minhas inscrições" tem o botão para baixar a agenda atualizada depois de trocar de sessão) e compartilhamento no WhatsApp e LinkedIn com prévia (Open Graph)
- Pesquisa de satisfação: ao finalizar a atividade, quem participou recebe o convite para dar nota de 0 a 10 e comentar. A organização vê média, NPS, distribuição das notas e comentários, sem o nome de quem respondeu (os comentários e o CSV saem em ordem de nota e só com o dia, para a hora da resposta não identificar ninguém). Opcionalmente, o certificado só é liberado depois da resposta. Na atividade por sessão, a pessoa também pode dar nota e comentar cada sessão em que teve presença (opcional); a gestão mostra a média de cada sessão, e o CSV traz essas notas
- Comunicados: o organizador envia avisos por e-mail aos confirmados e/ou à lista de espera (as respostas vão para o e-mail dele), e o aviso aparece na página da atividade para os inscritos
- Tipos de vaga: divide as vagas (ex.: alunos e público externo, ou reservadas por turma), cada tipo com vagas e lista de espera próprias, código de acesso opcional e opção "só para alunos com matrícula confirmada". A atividade também pode ter um código de acesso geral (evento restrito)
- Duplicar atividade para a próxima edição: copia sessões, palestrantes, perguntas, tipos de vaga e configurações, deslocando todas as datas; as edições ficam ligadas
- Números da atividade: inscrições e desistências por dia, ocupação, comparecimento, frequência média, origem por tipo de vaga, matrícula e perguntas de escolha, e comparação entre edições
- Números do semestre (botão no painel do organizador): por semestre, atividades, inscrições confirmadas, pessoas diferentes, comparecimento médio, horas certificadas (participação, palestrante e apresentação), nota média e NPS da pesquisa, um quadro por tipo de atividade e a lista atividade por atividade, com CSV. O administrador vê todas as atividades da instituição; o organizador, as próprias
- Check-in no celular como aplicativo (PWA, em `/attendance/`): instala na tela inicial, funciona sem internet guardando as leituras no aparelho e envia quando a conexão volta, com contador ao vivo de presentes (inclui leituras de outros celulares). A câmera do celular exige HTTPS
- Crachás em PDF com nome, tipo de vaga e o QR da inscrição, em três formatos de folha (cartões para cortar, etiquetas A4 e etiquetas Carta), com ajuste fino de margem e opção de pular etiquetas já usadas
- Materiais pós-evento: arquivos (até 50 MB) e links liberados só para quem teve presença (ou para todos os inscritos), a partir do fim da atividade ou de uma data escolhida. Os arquivos ficam em `private_media/` (variável `PRIVATE_MEDIA_ROOT`), fora do `/media/` público: inclua essa pasta no backup
- Cadastro com confirmação de e-mail por código de 6 dígitos (vale 15 minutos, até 5 tentativas). Cada rede (IP) cria no máximo 40 contas por hora (`SIGNUPS_PER_IP_HOUR`; aumente se muita gente se cadastra da mesma rede, como num laboratório). A troca de e-mail em "Meus dados" também pede código. Sem confirmar, a conta não se inscreve, e cadastros não confirmados em 7 dias são apagados
- O cadastro e a troca de e-mail não revelam se um endereço já tem conta: a tela é sempre "enviamos um código". Se o e-mail já pertence a uma conta confirmada, o código não sai e o dono recebe um aviso com o usuário dele e o link de nova senha (no máximo 3 avisos por dia)
- Campo "Matrícula ou CPF" no cadastro: o aluno digita a matrícula, que precisa ser a mesma do e-mail institucional (Configurações > "Domínio do e-mail dos alunos", ex.: `aluno.fsa.br`) e só vale como matrícula de aluno depois que o código for confirmado. Convidados podem informar o CPF (dígitos conferidos). Uma conta que nunca confirmou o e-mail não segura o endereço: quem confirmar o código (ou redefinir a senha pelo link) fica com ele
- Privacidade (LGPD): aviso de privacidade público (`/privacidade/`), aceite no cadastro, cópia dos dados em JSON e exclusão da conta pelo próprio participante em "Meus dados" (anonimiza o cadastro, cancela inscrições futuras e mantém certificados já emitidos válidos). Na validação pública o CPF aparece mascarado. O contato para assuntos de privacidade fica em Configurações. Destinatário e conteúdo dos e-mails enviados são apagados depois de 30 dias (fica só a contagem dos envios), e na exclusão da conta na hora
- Inscrição confirmada não pode ser cancelada depois que a atividade começa (a presença e o certificado dependem dela); sair da lista de espera continua liberado
- Limite de tentativas (guardado no banco, vale para todos os processos): login do portal e do /admin/ (em 15 minutos: 5 erros para o mesmo usuário vindo do mesmo IP, 50 para o mesmo usuário de qualquer lugar, 100 por IP; quem erra a senha de outra pessoa a partir de casa não impede o dono de entrar, e o limite de 50 não vale no navegador em que o dono já entrou com a senha certa, que fica marcado por 180 dias num cookie assinado), recuperação de senha (3 por e-mail e 30 por IP por hora), envio de código (3 por pessoa e 60 por IP por hora; os limites por IP são altos porque um laboratório inteiro sai pelo mesmo IP), código de acesso de evento (10 erros por pessoa por hora) e validação de certificado (30 códigos inexistentes por IP por hora)

**Organizadores e administradores**
- Painel do organizador: criar, editar, pré-visualizar, cancelar e excluir atividades, com busca, filtro por semestre e por situação e 20 atividades por página (catálogo, emissões avulsas e certificados de um lote também são paginados, e o lote tem busca por nome, matrícula ou turma)
- Atividade com inscrições não é excluída, apenas cancelada (o histórico é preservado)
- Sessões/aulas por atividade: criar, editar e excluir
- Chamada de trabalhos por evento (botão "Chamada de trabalhos" na gestão da atividade): período de submissão próprio (até o término do evento), instruções, áreas temáticas e limite de autores. Aparece na página do evento quando ele está publicado. O autor envia título, resumo, palavras-chave, coautores e o arquivo (PDF, DOC, DOCX ou ODT, até 10 MB, guardado fora da pasta pública) e acompanha tudo em "Meus trabalhos". A organização cadastra avaliadores (qualquer conta do portal), designa por trabalho e escolhe na chamada se a avaliação é às cegas (o avaliador não vê autores nem o nome original do arquivo) ou aberta. O avaliador dá recomendação, nota de 1 a 5, comentários ao autor e comentários só para a organização. A organização aprova, não aprova ou pede correções; nas correções o autor manda nova versão e as avaliações reabrem. O autor do trabalho aprovado fica inscrito no evento automaticamente (mesmo lotado) e recebe o QR Code; o trabalho pode ser colocado numa sessão da programação (na atividade por sessão, o autor fica confirmado nela) e aparece na lista de trabalhos aprovados. Cada etapa avisa por e-mail; os trabalhos saem em CSV. Cada chamada limita os trabalhos por autor (padrão 5) e cada pessoa pode enviar até 10 arquivos por hora; o arquivo trocado sai do disco. Na avaliação às cegas, os metadados de PDF, DOCX e ODT (autor, empresa, autor das revisões) são apagados no envio, num processo separado com limite de memória (512 MB) e de tempo (20 s): um arquivo feito para travar o leitor derruba só esse processo, e o trabalho fica guardado como veio. DOCX e ODT que descompactam em mais de 60 MB (ou com uma parte acima de 25 MB) são recusados em qualquer chamada. Uma decisão pode ser reaberta (com motivo, histórico e aviso ao autor) enquanto não houver certificado de apresentação; evento cancelado não recebe decisão e, com o evento finalizado, a aprovação não inscreve. Avaliador removido da chamada perde o acesso aos arquivos, e a tela avisa quando o nome ou o e-mail de um avaliador aparece entre os coautores. Quem avalia ou coordena vê o item "Avaliações" no menu, com o número de avaliações pendentes (não há papel de revisor na conta: a permissão é por chamada e por trabalho). Para não refazer essas contas a cada página, o menu guarda o resultado na sessão por 5 minutos e o recalcula ao abrir "Avaliações"; quem acabou de ser incluído como avaliador ou na equipe de check-in pode levar até 5 minutos para ver o item. A organização pode pôr pessoas na **coordenação da chamada**, que cuidam só dela (avaliadores, distribuição, decisões e arquivos), sem acesso às inscrições nem à gestão do evento. Como a coordenação vê os autores e os arquivos, ela entra com verificação em duas etapas, como a organização. Para incluir avaliador sem conta, basta informar o e-mail: a pessoa recebe um convite (vale 14 dias, uma vez só), cria a conta já confirmada ou entra com a que tiver. Se a conta logada tiver outro e-mail (link repassado), a tela mostra o e-mail do convite e pede uma confirmação a mais; em todo aceite, quem cuida da chamada recebe um aviso com a conta usada. Cada pessoa envia até 30 convites por hora. Os avaliadores (com as áreas em comum) podem ser trazidos de outra chamada, e duplicar a atividade leva a chamada junto (regras, áreas, critérios, avaliadores e coordenação; sem os trabalhos). O avaliador pode recusar uma designação (conflito de interesse, falta de tempo ou fora da área): a organização e a coordenação são avisadas, e a distribuição automática não escolhe de novo a mesma pessoa para aquele trabalho. A chamada pode ter **critérios de avaliação** (um por linha): cada um recebe nota de 1 a 5, a nota do trabalho é a média, a tela do trabalho mostra as notas por critério e o CSV traz a média de cada critério; os critérios não mudam depois da primeira avaliação entregue. Cada avaliação tem prazo ("Prazo para avaliar" na chamada, padrão 14 dias a partir da designação ou da nova versão, sem passar do início do evento): o avaliador recebe um lembrete 2 dias antes e, se atrasar, mais um a cada 3 dias (no máximo 3), num e-mail só com todos os trabalhos pendentes dele; a tela da chamada mostra os pendentes e atrasados de cada avaliador, com o botão "Lembrar". "Distribuir automaticamente" completa cada trabalho até o número pedido de avaliadores, respeitando as áreas que cada um avalia (marcadas na lista de avaliadores; nenhuma marcada, todas), sem o autor nem quem aparece entre os coautores, e começando por quem tem menos trabalhos; depois, cada trabalho pode ser ajustado na tela dele
- Escolha de sessões e certificado por sessão (opção na atividade, em "Sessões"): a pessoa se inscreve no evento, que continua com as suas vagas, e marca as sessões que vai assistir. Cada sessão tem o seu número de vagas e a sua lista de espera, chamada sozinha por ordem de chegada (com aviso por e-mail) quando alguém sai ou a organização aumenta as vagas. Com prazo de confirmação na atividade, a vaga da sessão fica reservada e a pessoa confirma em "Sessões" até o prazo informado no e-mail; sem confirmar, a vaga vai para o próximo da fila. Dá para entrar, trocar e sair de uma sessão em "Minhas inscrições" até ela começar; sessões no mesmo horário não podem ser escolhidas juntas. O check-in de cada sessão aceita só quem está confirmado nela, e ao finalizar sai um certificado por sessão escolhida com presença, com o título da sessão, o nome do evento, a data e a carga horária da sessão (a informada ou a duração arredondada). Cursos e atividades sem a opção continuam como antes: um certificado pela frequência no conjunto das sessões. O modo não muda depois de emitidos os certificados. Mudou o horário ou o local de uma sessão: quem vai a ela pode ser avisado por e-mail, e a tela mostra quantas pessoas ficaram com sessões no mesmo horário. A gestão mostra quantos inscritos ainda não escolheram sessão, com um botão para avisá-los. O lembrete de véspera é de cada sessão escolhida (um e-mail por pessoa), e o PDF de frequência e os números da atividade contam as presenças sobre as sessões escolhidas
- Controle de presença por sessão: check-in manual ou por QR Code pela câmera do navegador. A organização e a equipe de check-in têm o item "Check-in" no menu e, no celular, o botão flutuante "Ler QR Code": com uma sessão acontecendo agora (de 1 hora antes do início até 1 hora depois do fim), ele abre o leitor dela direto; com várias, abre a lista com as atuais primeiro. O app de check-in instalado na tela inicial tem o mesmo atalho no ícone. Quem chegou sem inscrição é inscrito na hora pela tela de presença ("Chegou sem inscrição?"), pela conta do portal (com o e-mail já confirmado, como em "Incluir participante"), já com a presença registrada: a equipe de check-in faz isso havendo vaga, e a organização também acima das vagas
- Relatório da sessão (botão "Relatório" em cada sessão da gestão): presença, comparecimento, nota média e comentários da sessão na pesquisa, para imprimir ou enviar por e-mail aos palestrantes dela (com os nomes de quem esteve, nunca e-mails ou documentos)
- Frequência por inscrito e lista de espera na tela de gestão
- Inclusão e ajuste de inscrições pela organização: na gestão da atividade, "Incluir participante" inscreve uma conta do portal, pelo usuário de acesso ou pelo e-mail confirmado (num tipo de vaga, se houver), mesmo com o evento lotado, e o nome de cada inscrito abre a ficha da inscrição, onde a organização cancela (com motivo obrigatório) ou, na atividade por sessão, coloca e tira a pessoa das sessões. A pessoa é avisada por e-mail e cada mudança fica no histórico da inscrição (quem fez, quando e por quê). Inscrição com certificado emitido não é cancelada por ali. Para turmas inteiras, "Incluir em lote" aceita uma lista de usuários ou e-mails (colada de uma planilha ou num CSV, até 500), mostra antes o que vai acontecer com cada linha (inscrever, já inscrito, conta não encontrada, e-mail não confirmado, conta desativada, repetido) e só inscreve depois da confirmação, com o mesmo e-mail e QR Code do "Incluir participante"
- Finalizar atividade: fecha o check-in e emite os certificados de quem atingiu a frequência mínima (padrão 75%, configurável por atividade)
- Certificados de palestrante e de apresentação de trabalho (opções na seção "Certificado" da atividade, ligadas por padrão): ao finalizar, cada palestrante cadastrado recebe um certificado com as sessões em que fala e a soma das horas delas (sem sessões ligadas, vale o período e a carga horária da atividade), e cada autor de trabalho aprovado na chamada recebe o de apresentação, com todos os autores, o evento e as horas da sessão em que apresentou: o autor de contato e cada coautor com e-mail informado na submissão (no campo de coautores, ex.: `Maria Souza (Fatec) maria@exemplo.com`; o e-mail não aparece para avaliadores nem na lista pública). O coautor recebe o PDF por e-mail e, com conta confirmada nesse e-mail, vê o certificado em "Minhas horas". O palestrante não precisa de conta: com e-mail cadastrado, recebe o PDF anexo; se tiver conta confirmada com esse e-mail, o certificado também aparece em "Minhas horas". O autor vê o seu em "Minhas horas". A gestão da atividade lista esses certificados com PDF, correção e revogação; palestrante com certificado não pode ser removido, e quem for incluído depois da finalização entra em "Emitir certificados pendentes". Opcional: só emitir o de apresentação quando o autor teve presença na sessão do trabalho (o coautor precisa estar inscrito no evento com o e-mail informado e ter a presença registrada)
- Equipe de check-in por atividade: monitores e voluntários (com conta de participante) registram presença, abrem e fecham o check-in, sem ver e-mails, relatórios ou configurações
- Mudou a data ou o local de uma atividade com inscritos: os inscritos podem ser avisados por e-mail com o antes e o depois, e o lembrete de véspera vale para a data nova
- Certificado emitido pode ser corrigido (nome ou matrícula, mantendo o código) ou revogado, com motivo e o antes e depois na auditoria; presença lançada por engano pode ser desfeita, e a remoção fica num registro permanente (quem removeu, quando, de qual IP e quem tinha registrado), visível para a organização na tela de presença da sessão e protegido contra alteração no banco
- Verificação em duas etapas para administradores, organizadores e coordenação de chamadas de trabalhos: código de 6 dígitos de um aplicativo autenticador (Google Authenticator, Microsoft Authenticator, 2FAS etc.) depois da senha, em todo login (inclusive no /admin/), com 10 códigos de recuperação de uso único. A sessão dessas contas dura no máximo 8 horas
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

Copie `.env.example` para `.env` (`Copy-Item .env.example .env` no Windows, `cp .env.example .env` no Linux/macOS) e, para desenvolvimento, mude `DEBUG=True` e preencha `DB_PASSWORD` com uma senha à sua escolha (o `docker-compose.yml` cria o banco com ela). Com isso os demais valores padrão já funcionam com o banco do Docker. A chave de exemplo só é aceita com `DEBUG=True` e acesso pela própria máquina; em qualquer servidor, gere uma `SECRET_KEY` própria.

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

Enquanto o envio estiver desligado em **Configurações > E-mail** (o padrão), as mensagens (confirmações, recuperação de senha, certificados) aparecem no terminal do `runserver`. Fora do modo de desenvolvimento (`DEBUG=False`), só o destinatário e o assunto vão para o log, nunca o corpo (que traz códigos de confirmação e links de nova senha); `EMAIL_LOG_BODY=True` mostra o corpo também.

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

Testes de navegador (`core/test_browser.py`): inscrição, check-in manual, finalização e download do certificado num Chromium de verdade, mais a verificação de acessibilidade do axe-core (problemas "sérios" e "críticos" reprovam) nas telas públicas e nas usadas nesse fluxo. `core/test_browser_organizer.py` faz a mesma verificação nas telas da organização e da administração (gestão da atividade, inscrição em lote, números, chamada de trabalhos, avaliação, check-in também na largura de celular, configurações, usuários, auditoria e emissão avulsa). Instale o navegador uma vez; sem ele, esses testes são pulados:

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
python manage.py test core.test_browser core.test_browser_organizer
```

A CI roda tudo isso e, num passo separado, o `shellcheck` dos scripts de `infra/` e `scripts/`, o build da imagem Docker e um `manage.py check` dentro dela.

O usuário do banco precisa de permissão para criar bancos (`CREATEDB`); o do `docker-compose.yml` já tem.

## Produção

### Antes de abrir para usuários reais

Confira item por item (os detalhes estão nas seções abaixo):

- [ ] `SECRET_KEY` própria, com cópia guardada fora do servidor (a senha do SMTP e a verificação em duas etapas dependem dela)
- [ ] `DEBUG=False`; `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` e `SITE_URL` com o endereço público
- [ ] **Domínio definitivo antes do primeiro certificado real**: o QR Code e o link de validação guardam o `SITE_URL` da emissão. Com um endereço provisório da AWS (`*.cloudfront.net` ou `*.elb.amazonaws.com`), o `check --deploy` avisa (`arena.W005`) e as telas de emissão mostram um alerta
- [ ] Aviso de privacidade (`/privacidade/`) revisado pela instituição, com o contato em Configurações
- [ ] Senhas do PostgreSQL longas e aleatórias em `DB_PASSWORD` e `DB_MIGRATE_PASSWORD` (o `check --deploy` avisa, `arena.W004`, se alguma for fraca ou a do exemplo)
- [ ] Imagem base fixada pelo digest: `bash scripts/pin_base_image.sh` (veja "Dependências")
- [ ] Log de acesso do servidor (`docker compose logs web`, ou o arquivo do proxy) visto só por quem administra: os links de convite e de nova senha passam por ele (na AWS, o mesmo vale para o bucket de registros de acesso)
- [ ] Banco com dois papéis: um dono das tabelas, só para migrações e backup, e outro para a aplicação (veja "Banco: dois papéis")
- [ ] Proxy reverso com HTTPS, `NUM_PROXIES=1` e **limite de tamanho de envio** (veja "Proxy reverso")
- [ ] HSTS ligado com valor baixo (`SECURE_HSTS_SECONDS=3600`), aumentando depois que o HTTPS estiver estável
- [ ] `ADMIN_URL` próprio (o painel sai de `/admin/`) e, se possível, restrito por IP no proxy
- [ ] `python manage.py check --deploy` sem avisos que você não tenha decidido aceitar
- [ ] `python manage.py validate_constraints` rodado uma vez depois do `migrate` (veja "Regras de data do banco")
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

A cada subida, o serviço `migrate` aplica as migrations e termina; depois o `web` roda o gunicorn na porta 8000. O serviço `worker` envia a fila de e-mails (veja "Fila de e-mails") e o `zips` monta os ZIPs de lotes grandes. O HTTPS fica a cargo de um proxy reverso (nginx, Caddy, Traefik) na frente dele.

### Opção 3: AWS

`infra/setup.sh` monta tudo na AWS (us-east-1) e pode rodar de novo a cada atualização: ECS Fargate com dois serviços, o web (gunicorn, atrás do ALB, `WEB_COUNT` tarefas, padrão 1) e o worker (fila de e-mails, lembretes, vagas da fila, faxina e ZIPs, sempre 1 tarefa), RDS PostgreSQL 17 criptografado, EFS criptografado para `media/` e `private_media/` (os arquivos enviados sobrevivem aos deploys), ALB e os segredos no SSM. Precisa do aws cli configurado, docker, python3 e curl.

```bash
./infra/setup.sh                                   # só o seu IP atual abre o site (HTTP)
DOMAIN=eventos.exemplo.br CERT_ARN=arn:aws:acm:... ALLOWED_CIDR=0.0.0.0/0 ./infra/setup.sh   # HTTPS, aberto
./infra/stop.sh                                    # para os containers (STOP_DB=1 para o RDS também)
./infra/teardown.sh                                # apaga tudo (FINAL_SNAPSHOT=1 guarda uma cópia do banco)
CLOUDFRONT=1 ./infra/setup.sh                     # HTTPS aberto a todos, sem domínio (https://dxxxx.cloudfront.net)
CLOUDFRONT=1 DOMAIN=eventos.exemplo.com.br CERT_ARN=arn:aws:acm:us-east-1:... ./infra/setup.sh  # CloudFront no seu domínio
ALERT_EMAIL=voce@exemplo.br ./infra/setup.sh       # com alarmes por e-mail
STAGE=prod DOMAIN=... CERT_ARN=... ALERT_EMAIL=... ./infra/setup.sh   # outro ambiente, separado do de testes
```

- O security group do ALB é só do script: a cada execução, regras de entrada em outras portas (criadas à mão no console, por exemplo) são removidas. Com o CloudFront, a lista de endereços dele ocupa cerca de 55 das 60 regras que a AWS permite por security group; se um dia precisar de mais regras, peça o aumento da cota.
- `CLOUDFRONT=1` põe o CloudFront na frente do ALB: o site ganha HTTPS num endereço da AWS (`https://dxxxx.cloudfront.net`, mostrado no final), sem precisar de domínio, e fica aberto a todos. O ALB passa a aceitar só o CloudFront (o endereço `elb.amazonaws.com` deixa de abrir direto), as páginas nunca ficam em cache (só os arquivos de `/static/`), e a aplicação reconhece o HTTPS pelo cabeçalho `CloudFront-Forwarded-Proto` (`PROXY_SSL_HEADER`) e o IP de quem acessa com `NUM_PROXIES=2`. A primeira execução espera o CloudFront ficar pronto (5 a 15 minutos). Repita `CLOUDFRONT=1` em todo deploy: sem ele, o ALB volta a aceitar só o seu IP. O `teardown.sh` desativa e apaga a distribuição.
  - **Só o seu CloudFront entra no ALB.** A lista de endereços do CloudFront é a mesma para todos os clientes da AWS, então o script também cria um cabeçalho secreto (`X-Origin-Verify`, guardado no SSM em `origin_secret`) que o seu CloudFront manda a cada pedido. O ALB só encaminha os pedidos com ele; os outros (inclusive uma distribuição de outra pessoa apontada para o seu ALB, que passaria por fora do seu WAF) recebem 403. Na primeira vez, o script só passa a exigir o cabeçalho depois que o CloudFront termina de publicar, para o site não cair. Para trocar o segredo, apague o parâmetro `origin_secret` no SSM e rode o `setup.sh` de novo.
  - **Com domínio** (`DOMAIN` e `CERT_ARN`, com o certificado do ACM em us-east-1 e `REGION=us-east-1`): o site fica em `https://DOMAIN`, o mesmo certificado vale no CloudFront e no ALB, e o trecho do CloudFront até o ALB também passa a ser HTTPS (sem domínio, esse trecho é HTTP dentro da AWS, o que serve para testes, mas não para eventos reais). No final, o script mostra o CNAME a criar no DNS: `DOMAIN` apontando para o `dxxxx.cloudfront.net`. Ao trocar de "sem domínio" para "com domínio" (ou o contrário), o site fica fora do ar por alguns minutos, enquanto o CloudFront publica a mudança. Para o volume de testes, o custo fica na faixa gratuita ou em centavos. O CloudFront espera a resposta do site por até 60 segundos (`CF_ORIGIN_TIMEOUT`); se algum relatório em PDF muito grande der erro 504, peça à AWS o aumento da cota "Response timeout per origin" e rode com um valor maior, até 180.
- Registros de acesso: o ALB e o CloudFront gravam cada pedido (IP, página, resposta, tempo) no bucket S3 `arena-events-<STAGE>-logs-<conta>`, privado e criptografado, em `alb/` e `cloudfront/`. Servem para investigar um incidente (quem acessou o quê, de onde). São apagados sozinhos depois de `LOG_DAYS` dias (padrão 60) e custam centavos por mês no volume de vocês. `ACCESS_LOGS=0` desliga a gravação (o bucket continua, com o que já tem); o `teardown.sh` apaga o bucket, a não ser com `KEEP_LOGS=1`. Para consultar, baixe os arquivos do período (`aws s3 cp s3://<bucket>/alb/ . --recursive --exclude "*" --include "*2026/10/23*"`) ou use o Athena.
- WAF (firewall da AWS no ALB, ou no CloudFront com `CLOUDFRONT=1`, onde ele vê o IP real de quem acessa): liga sozinho quando o site é aberto a todos (`ALLOWED_CIDR=0.0.0.0/0` ou `CLOUDFRONT=1`) e pode ser forçado com `WAF=1` ou desligado com `WAF=0`. Usa as regras gerenciadas da AWS (ataques comuns, entradas maliciosas conhecidas e IPs de má reputação) e bloqueia o IP que passar de `WAF_RATE_LIMIT` requisições em 5 minutos (padrão 3000, alto porque um laboratório inteiro sai pelo mesmo IP). As regras de tamanho e de script no corpo da requisição só contam, sem bloquear, para não barrar o envio de arquivos e textos. Custa cerca de US$ 8/mês. Nas primeiras semanas, vale abrir o console do WAF ("Web ACLs" > a do ambiente > "Sampled requests") depois de cada evento para ver se alguma requisição legítima foi bloqueada; se for, a regra pode passar para o modo de contagem.
- `STAGE` (padrão `test`) dá nome ao ambiente: cada um tem os seus recursos, banco, arquivos e segredos (`/arena-events/<STAGE>/` no SSM). Use o mesmo `STAGE` no `stop.sh` e no `teardown.sh`; fora do `test`, o teardown pede o nome do ambiente para confirmar.
- `ALERT_EMAIL` cria alarmes do CloudWatch com aviso por e-mail (SNS): site fora do ar (nenhuma tarefa saudável por 3 minutos), 10 ou mais erros 5xx em 5 minutos, banco com menos de 2 GB livres, CPU do banco acima de 80% por 15 minutos e worker sem tarefa rodando por 10 minutos (este liga o Container Insights do cluster, alguns dólares por mês). A AWS manda um e-mail de confirmação: só depois de clicar no link os alarmes chegam. O `stop.sh` pausa os alarmes, e o `setup.sh` religa.
- Backup automático do banco por `DB_BACKUP_DAYS` dias (padrão 7, vale também para um banco que já existia). `DB_DELETION_PROTECTION=1` impede apagar o banco por engano (padrão fora do `test`); o `teardown.sh` desliga a proteção antes de apagar.
- O `/admin/` do Django fica num endereço difícil de adivinhar, gerado na primeira vez e guardado no SSM (`admin_url`); o script mostra o endereço no final.
- Com HTTPS, o HSTS começa em 1 dia (`HSTS_SECONDS=86400`); depois de confirmar que o HTTPS está estável, rode de novo com `HSTS_SECONDS=31536000`.

- Sem `DOMAIN` e `CERT_ARN`, o acesso é por HTTP e o security group do ALB só aceita o seu IP (se o seu IP mudar, rode o setup de novo). Para abrir a outras pessoas, use HTTPS: um certificado do ACM e um CNAME do domínio para o endereço do ALB.
- As tarefas ficam nas subnets públicas com IP público (saída para ECR, SSM, logs e SMTP sem NAT), mas só o ALB chega nelas. O RDS e o EFS ficam nas subnets privadas.
- Banco com dois papéis: o usuário mestre do RDS (`arena_admin`) só roda as migrações e o `setup_db_roles`, numa tarefa separada antes de cada deploy (se ela falhar, o serviço não é atualizado); a aplicação usa `arena_app`. A conexão confere o certificado do servidor (`DB_SSLMODE=verify-full`, com a CA do RDS baixada pelo script para `certs/`).
- Primeiro administrador: o script mostra o comando `aws ecs execute-command ... createsuperuser` (precisa do Session Manager plugin).
- Custo aproximado ligado o tempo todo: US$ 65 a 85/mês (Fargate do web e do worker, ALB, RDS, IPs públicos e EFS pelo uso), mais uns US$ 8 com o WAF ligado. Cada tarefa web a mais (`WEB_COUNT`) soma cerca de US$ 20.
- O repositório do ECR tem tags imutáveis e guarda as 15 imagens mais recentes; sem alterações desde o último deploy, o build é pulado. As senhas passam por arquivos temporários, nunca pela linha de comando.

### Opção 2: Sem Docker

```bash
pip install --require-hashes -r requirements.lock   # Linux com Python 3.12; no Windows: pip install -r requirements.txt
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

O comando `python manage.py setup_db_roles` (rodado com o dono) cria o papel da aplicação, ou atualiza a senha e as permissões dele, e faz as tabelas criadas por migrações futuras já nascerem com as mesmas permissões. Pode rodar quantas vezes quiser. No Docker, o serviço `migrate` do `docker-compose.yml` roda `migrate` e `setup_db_roles` a cada subida, antes do `web`. Ele é o único que recebe `DB_MIGRATE_USER` e `DB_MIGRATE_PASSWORD`: o `web` e o `worker` sobem com essas variáveis em branco, então uma falha no servidor web não expõe a senha do dono.

**Instalação nova com Docker:** no `.env`, defina os dois papéis com nomes diferentes:

```env
DB_MIGRATE_USER=arena_owner
DB_MIGRATE_PASSWORD=<senha forte>
DB_USER=arena_app
DB_PASSWORD=<outra senha forte>
```

O PostgreSQL do `docker-compose.yml` cria o `DB_MIGRATE_USER` na primeira subida, e o serviço `migrate` cria o `arena_app`.

**Banco que já existe (um usuário só até agora):** o usuário atual vira o dono, e você cria um novo para a aplicação. No `.env`, passe o usuário e a senha atuais para `DB_MIGRATE_USER` e `DB_MIGRATE_PASSWORD`, e coloque em `DB_USER` e `DB_PASSWORD` um nome e uma senha novos. Depois, `docker compose --profile prod up -d` (ou, sem Docker, `python manage.py migrate` seguido de `python manage.py setup_db_roles`).

Sem Docker, o dono precisa poder criar papéis (`CREATEROLE`). Se não puder, crie o papel da aplicação uma vez como superusuário (`CREATE ROLE arena_app LOGIN PASSWORD '...';`) e rode `setup_db_roles`, que aplica as permissões.

Sem `DB_MIGRATE_USER`, um único usuário faz tudo, como no desenvolvimento e nos testes (que precisam de um usuário com permissão para criar o banco de teste).

### Regras de data do banco

As regras que impedem datas trocadas (fim antes do início em atividades, sessões e certificados) foram criadas sem conferir os registros antigos, para não travar o deploy. Para conferir tudo e deixá-las valendo também para o que já existia:

```bash
python manage.py validate_constraints --conferir   # só lista o que falta
python manage.py validate_constraints              # valida as regras sem pendências
```

Se algum registro antigo violar uma regra, o comando mostra os ids; corrija pelo `/admin/` e rode de novo. Com `DB_MIGRATE_USER` configurado, o comando usa o dono das tabelas automaticamente (no Docker, pelo serviço que tem a senha do dono: `docker compose --profile prod run --rm migrate python manage.py validate_constraints`).

### Verificação em duas etapas

Em produção (`DEBUG=False`), administradores, organizadores, quem acessa o `/admin/` e quem coordena alguma chamada de trabalhos (vê os autores, baixa os arquivos e decide) precisam cadastrar um aplicativo autenticador no primeiro acesso: o sistema mostra um QR Code, pede um código para confirmar e entrega 10 códigos de recuperação (aparecem uma vez só; guarde-os). Depois disso, todo login pede o código do aplicativo. Em **Meus dados > Verificação em duas etapas** dá para gerar novos códigos de recuperação ou trocar de celular.

- Celular perdido e sem códigos de recuperação: depois de confirmar a identidade da pessoa, um administrador do servidor executa `python manage.py reset_mfa <usuário ou e-mail>` (no Docker: `docker compose exec web python manage.py reset_mfa <usuário>`). No próximo acesso ela cadastra de novo.
- O segredo de cada cadastro fica criptografado com uma chave derivada da `SECRET_KEY`. Se a `SECRET_KEY` mudar, todos precisam cadastrar de novo (use `reset_mfa` para cada conta).
- `MFA_REQUIRED=False` deixa a verificação opcional (só quem ativar em "Meus dados"). Com `DEBUG=True` ela já é opcional, para não atrapalhar o desenvolvimento.
- `PRIVILEGED_SESSION_HOURS` (padrão 8) é a duração máxima da sessão dessas contas; participantes continuam com 2 semanas.
- `reset_mfa` também encerra todas as sessões abertas da conta: quem estava com o celular perdido (ou com a sessão roubada) precisa entrar de novo.
- O código também é limitado por conta: além de 5 tentativas erradas por endereço IP, mais de 20 erros em 15 minutos bloqueiam a conta inteira por esse tempo, venham de onde vierem.

**Confirmação de identidade.** Ações sensíveis pedem a senha de novo (e o código do aplicativo, se a conta usa verificação em duas etapas) quando o último login ou confirmação foi há mais de `REAUTH_MINUTES` minutos (padrão 15): revogar ou corrigir certificado, editar ou cancelar lote, revogar certificados de um lote, salvar em **Configurações > Instituição e certificados** (assinatura, arte e nome da instituição; abrir a tela para consultar não pede), **Configurações > E-mail** (inclusive o envio de teste), as ações do painel de usuários e o "Rodar agora" da faxina. Assim uma sessão esquecida aberta não basta para essas ações.

### Segurança do navegador (CSP)

Toda página sai com `Content-Security-Policy`: só scripts do próprio portal (pasta `static/js/`; o Bootstrap também fica no portal, em `static/vendor/bootstrap/`, sem depender de outro site), nada de script dentro do HTML, e o portal não pode ser aberto dentro de outro site. A câmera só é liberada (`Permissions-Policy`) nas telas de leitura de QR. Se algo for bloqueado, o navegador avisa o portal e o aviso aparece no log (`arena.csp`).

- `CSP_MODE=report` só registra no log, sem bloquear (útil para investigar um problema); `CSP_MODE=off` desliga.
- Ao mudar templates, não use `<script>` com código nem `onclick=""`: coloque o código em `static/js/app.js` (ou num arquivo novo) e ligue pelo atributo `data-*`. O teste `core.test_security_headers` confere isso nas principais telas.

Ao sair do portal, o navegador apaga os dados do check-in guardados no aparelho (lista de inscritos e telas offline). Se ainda houver leituras sem enviar, o portal pergunta antes.

### Usuários e permissões

O que cada pessoa pode fazer depende só do **papel** da conta: Participante (se inscreve e acompanha as próprias horas), Organizador (cria e gerencia as próprias atividades e emissões avulsas) e Administrador (gerencia tudo, inclusive as Configurações). Os grupos e permissões do Django não são usados.

As contas são administradas em **Configurações > Usuários**: busca e filtros (papel, situação, e-mail confirmado, duas etapas), e na página de cada pessoa as ações de mudar o papel, desativar ou reativar (desativar encerra as sessões abertas), redefinir as duas etapas, enviar o link de nova senha (o administrador nunca digita a senha de ninguém), reenviar o código de confirmação e excluir a conta (anonimiza, como a exclusão pelo próprio participante). Regras:

- toda ação pede a senha de novo se o login tiver mais de `REAUTH_MINUTES` minutos e fica no histórico da conta (quem fez, quando, o que mudou);
- ninguém altera a própria conta pelo painel, e o portal nunca fica sem um administrador ativo;
- só superusuário altera conta de superusuário.

**Equipe nova:** em **Configurações > Usuários > Convidar para a equipe**, informe o e-mail e o papel (organizador ou administrador). A pessoa recebe um link (vale 7 dias, uma vez só), cria o usuário e a senha, e a conta já nasce com o papel e o e-mail confirmado; no primeiro acesso, cadastra o aplicativo autenticador. Convites abertos podem ser cancelados ou reenviados na mesma tela (reenviar gera um link novo, com prazo novo, e o anterior deixa de valer).

**Quem sai da equipe:** na página da pessoa, "Transferir atividades e emissões" passa tudo o que ela organizou para outra conta de organizador ou administrador. Quando a instituição não tem signatário próprio, os certificados saem com o nome de quem organizou; esse nome fica guardado na atividade e no lote na transferência (e também na exclusão da conta), então os certificados já emitidos não mudam. Na transferência, dá para escolher que a pessoa que recebe passe a assinar as atividades que ainda não terminaram; as finalizadas e as emissões avulsas mantêm sempre a assinatura original.

**Conferência periódica:** "Exportar CSV" na lista baixa as contas do filtro atual (papel, situação, duas etapas, último acesso). Como tem dados pessoais de todos, pede a senha de novo.

O `/admin/` do Django fica para emergências: lá a tela de usuário não mostra grupos e permissões, papel e e-mail são só leitura e não é possível apagar contas. Para criar o primeiro administrador: `python manage.py createsuperuser` (o superusuário já tem acesso a tudo, inclusive ao painel de usuários).

### Painel administrativo, banco remoto e outros avisos

- `ADMIN_URL` (ex.: `ADMIN_URL=gestao-arena/`) troca o endereço do painel `/admin/`. Se possível, restrinja esse caminho por IP no proxy.
- Banco em outro servidor: `DB_SSLMODE=require` (ou `verify-full`) criptografa a conexão.
- `python manage.py check --deploy` mostra também os avisos do Arena+ (`arena.W001` a `W004`): `SERVE_MEDIA` ligado em produção, banco remoto sem criptografia, painel no endereço padrão e senha do banco fraca.
- A tela **Configurações > E-mail > Enviar teste** faz o servidor conectar no host e porta informados: deixe-a só com quem administra o servidor.
- A fila de e-mails entrega "pelo menos uma vez": se o processo for derrubado no instante entre o servidor de e-mail aceitar a mensagem e o portal anotar o envio, ela sai de novo depois de 10 minutos. É raro e inofensivo (um aviso repetido).

### Exportação da auditoria

`python manage.py export_audit --pasta <pasta>` grava a auditoria de certificados, as presenças removidas e o histórico de alterações de contas (os três só aceitam inclusões, inclusive no banco) em arquivos JSON Lines encadeados por hash: cada linha leva o hash da anterior, então alterar, apagar ou reordenar qualquer linha quebra a cadeia. Cada exportação continua de onde a anterior parou. `--conferir` verifica todos os arquivos da pasta.

Com `AUDIT_EXPORT_DIR` no `.env`, o serviço `worker` exporta uma vez por dia. Duas exportações ao mesmo tempo (o worker e um comando manual) não acontecem: a segunda é recusada com aviso. Quando sai algo novo, o portal manda por e-mail o último registro e o último hash de cada tipo, para `AUDIT_EXPORT_EMAIL` ou, sem ele, para o e-mail de alertas de erro. Guarde essas mensagens: ficam fora do servidor e mostram depois se alguém refez a cadeia inteira (o hash final mudaria). No Docker, use uma pasta dentro do volume de arquivos privados, que já entra no backup: `AUDIT_EXPORT_DIR=/app/private_media/auditoria-exportada`. Para valer como prova, copie a pasta para um armazenamento que não permita alterar nem apagar (por exemplo, um bucket com bloqueio de objetos) e fora do servidor.

### Manutenção e saúde

O serviço `worker` do Docker, além da fila de e-mails, faz sozinho:

- a cada 5 minutos: devolve à fila de espera as vagas não confirmadas no prazo e apaga o conteúdo dos e-mails antigos;
- de hora em hora: lembretes de véspera;
- uma vez por dia: apaga sessões de login vencidas, códigos de confirmação com mais de 7 dias, contadores de tentativas antigos, cadastros de participante não confirmados em 7 dias, cópias de imagens do certificado sem uso há 30 dias e convites de equipe não aceitos, vencidos ou cancelados há mais de 30 dias; e exporta a auditoria, se `AUDIT_EXPORT_DIR` estiver definido.

Sem Docker, agende `python manage.py run_maintenance` uma vez por dia (além de `process_outbox`, `send_reminders` e `expire_waitlist_offers`, já descritos).

Com mais de um `worker` rodando (por exemplo, duas tarefas na AWS), cada rotina roda uma vez só por intervalo: o horário da última execução fica no banco, e o primeiro worker a pegar a vez faz o trabalho.

Em **Configurações > Manutenção** (só administradores) aparecem o resultado da última faxina, tarefa por tarefa, as execuções recentes e a situação da exportação da auditoria (arquivos, último registro e hash, quanto falta exportar). A tela avisa quando a faxina não roda há mais de um dia e meio (worker parado ou agendamento esquecido) e tem o botão "Rodar agora", que pede a senha de novo se o login for antigo. A faxina pedida roda em segundo plano (pelo `worker`, ou pelo próprio servidor com `OUTBOX_MODE=thread`), e a tela se atualiza sozinha até terminar.

A rota `/saude/` responde `{"situacao": "ok", "banco": "ok"}` (código 200) quando a aplicação e o banco estão funcionando, e 503 quando não. Ela atende por `http://127.0.0.1:8000/saude/` mesmo fora do `ALLOWED_HOSTS` e sem HTTPS, para o `healthcheck` do Docker (o `web` aparece como `healthy` em `docker compose ps`, e o `worker` só sobe depois dele) e para um monitor externo, como o UptimeRobot.

As datas das atividades, sessões e emissões avulsas (término depois do início, inscrições até o término) também são conferidas pelo banco. Ao atualizar, se algum registro antigo tiver datas invertidas, o `migrate` lista quais são para corrigir pelo /admin/; o sistema continua funcionando.

### Imagens enviadas (`/media/`)

O ideal é o proxy reverso servir a pasta `media/`. Em instalações pequenas, `SERVE_MEDIA=True` faz o próprio Django servir.

As imagens (capa, foto de palestrante, logo, fundo e assinatura do certificado) são aceitas só em **JPG, PNG ou WebP**. Os leitores dos outros formatos do Pillow (GIF, BMP, TIFF, EPS e dezenas de outros, onde aparece a maioria das falhas de segurança dele) ficam desligados quando o sistema sobe (`core/images.py`). Uma imagem antiga em outro formato continua aparecendo no site, mas pode deixar de sair no certificado: envie de novo em JPG, PNG ou WebP.

### E-mail

O servidor de envio é configurado dentro da plataforma, em **Configurações > E-mail** (visível para administradores):

- servidor SMTP ou relay, porta e segurança (STARTTLS, SSL/TLS ou nenhuma, para relay interno na porta 25);
- usuário e senha, opcionais (em branco para relay liberado pelo IP do servidor); a senha fica criptografada no banco;
- opção de não verificar o certificado, para relay interno com certificado autoassinado;
- e-mail e nome do remetente, "responder para" e tempo limite;
- botão **Enviar teste**, que mostra o erro do servidor quando algo falha.

Enquanto o envio estiver desligado nessa tela, os e-mails não saem: no log aparecem só o destinatário e o assunto (com `EMAIL_LOG_BODY=True`, ou em desenvolvimento, a mensagem inteira).

A senha do SMTP é criptografada com uma chave derivada da `SECRET_KEY`. Se a `SECRET_KEY` mudar, cadastre a senha de novo.

Defina também `SITE_URL` no `.env` (endereço público, usado nos links dos e-mails e no QR de validação dos certificados).

### Alerta de erros

Em **Configurações > E-mail**, o campo "E-mail para alertas de erro" recebe um aviso quando o portal dá erro 500, com o endereço da página e o detalhe técnico (no máximo 10 por hora).

### Dependências

As versões ficam fixas em `requirements.txt`: o servidor recebe exatamente o que foi testado. O Dependabot (`.github/dependabot.yml`) abre um pull request quando sai versão nova (pacotes Python, actions da CI e imagem base do Docker), e a CI roda os testes nele. Para atualizar à mão, troque a versão, rode `python manage.py test` e faça o deploy.

A CI só tem permissão de leitura no repositório, e as actions ficam fixas pelo hash do commit (o comentário ao lado mostra a versão), para que uma tag alterada por terceiros não rode código diferente.

**Pacotes conferidos por hash.** O `requirements.lock` tem as mesmas versões do `requirements.txt`, mais as dependências delas, cada uma com o hash dos arquivos publicados. O Docker e a CI instalam por ele com `--require-hashes`: um pacote adulterado no caminho é recusado. O lock vale para Linux com Python 3.12 (o servidor); no Windows, para desenvolver, continue usando `pip install -r requirements.txt`. Depois de mudar o `requirements.txt` (à mão ou por um pull request do Dependabot), gere o lock de novo com o comando que está no início dele (`pip install uv` antes, se precisar) e inclua os dois arquivos no commit; a CI falha se o lock estiver desatualizado.

**Imagem base fixada.** Rode uma vez `bash scripts/pin_base_image.sh` (precisa do Docker): ele troca o `FROM python:3.12-slim` do `Dockerfile` pela mesma imagem com o digest (`@sha256:...`), e o build passa a usar sempre exatamente essa imagem. Rode de novo quando o Dependabot avisar que saiu versão nova.

A CI também roda o `pip-audit`, que falha quando alguma versão fixada tem vulnerabilidade conhecida publicada (base do PyPI/OSV), e as regras de segurança do `ruff`. Para conferir na sua máquina: `pip-audit -r requirements.txt -r requirements-dev.txt`.

### Fila de e-mails

Os e-mails das ações do sistema (confirmações, finalização com certificados, comunicados, certificados avulsos) entram numa fila no banco e saem em segundo plano, um por um. A tela responde na hora, mesmo com centenas de participantes, e quem já recebeu não recebe de novo. Se o servidor de e-mail falhar, o sistema tenta de novo sozinho (1, 5, 15 e 60 minutos depois); depois de 5 tentativas a mensagem aparece como "falhou" em **Configurações > E-mail**, com o botão para tentar de novo. O botão só reenvia as falhas dos últimos 3 dias (`OUTBOX_RETRY_DAYS`): um aviso de vaga, um lembrete ou um código de semanas atrás chegaria fora de hora.

Por padrão (`OUTBOX_MODE=thread`) o próprio servidor envia, logo depois de cada operação. Como rede de segurança (ex.: o servidor reiniciou no meio de um lote), deixe também o comando abaixo rodando:

- **Docker**: o serviço `worker` do `docker-compose.yml` já faz isso (`python manage.py process_outbox --continuo`);
- **Linux (cron)**: `* * * * * cd /caminho/arena-events && venv/bin/python manage.py process_outbox`
- **Windows**: no Agendador de Tarefas, uma tarefa de minuto em minuto executando `venv\Scripts\python.exe manage.py process_outbox` na pasta do projeto.

No Docker, o serviço `web` já sobe com `OUTBOX_MODE=off` (definido no `docker-compose.yml`, vale mais que o `.env`): ele só coloca as mensagens na fila e o `worker` envia. Assim um reinício do servidor web no meio de um lote não atrasa nada. Sem Docker, use `OUTBOX_MODE=off` quando o `process_outbox` estiver agendado de minuto em minuto. Vários processos podem trabalhar ao mesmo tempo sem duplicar mensagens.

**ZIP de lotes grandes.** Na emissão avulsa, lotes com até 200 certificados baixam o ZIP na hora. Acima disso, o botão vira "Gerar ZIP de todos": o arquivo é montado em segundo plano (no Docker, pelo serviço `zips`, separado do `worker` para os e-mails não esperarem; sem Docker, pelo `process_outbox` agendado, um ZIP por vez entre os envios, ou pelo próprio servidor com `OUTBOX_MODE=thread`), a página se atualiza sozinha e mostra o link quando fica pronto. O arquivo fica em `private_media/zips/` por 24 horas e é descartado se o lote mudar (participantes novos, revogação ou edição dos dados).

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
docker compose stop web worker zips
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
