# GESTFILA

Sistema de gestão de senhas e filas para clínicas (multi-tenant, multi-clínica): totem público de geração de senha, painel de chamada para TV, tela de atendimento (recepção/coleta), dashboard operacional e relatórios gerenciais.

Toda a especificação funcional, o histórico de decisões de produto e as regras de negócio estão documentados em **[BLUEPRINT.md](BLUEPRINT.md)** — este README cobre só o setup técnico.

## Stack técnica

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.11+, Flask, Flask-SQLAlchemy, Flask-Login, Flask-SocketIO (`eventlet`) |
| Banco | MySQL 8 (driver PyMySQL) |
| Front (totem/painel/admin) | Jinja2 + JS puro + Socket.IO client |
| Servidor de produção | Gunicorn + worker `eventlet`, atrás de Apache2 (proxy reverso com suporte a WebSocket) — ver `deploy/` |
| Relatórios/export | pandas + openpyxl (CSV/Excel) + Chromium headless via Playwright (PDF) |

Detalhes completos na [seção 10 do BLUEPRINT.md](BLUEPRINT.md).

## Rodando localmente

1. **Criar e ativar o virtualenv**
   ```
   python -m venv venv
   # Windows
   venv\Scripts\activate
   # Linux/Mac
   source venv/bin/activate
   ```

2. **Instalar as dependências**
   ```
   pip install -r requirements.txt
   ```

   A exportação de relatórios em PDF (adendo 10.67) usa Chromium headless via
   Playwright — `pip install` sozinho baixa só o pacote Python, o navegador em
   si é um passo separado (uma vez só por máquina, não por deploy):
   ```
   playwright install chromium
   ```
   Sem isso, a tela funciona normalmente e CSV/Excel continuam exportando —
   só o botão "PDF" falha (erro tratado, com flash de aviso, não um 500 cru).

3. **Configurar variáveis de ambiente**

   Copie `.env.example` para `.env` e preencha com os valores do seu ambiente (nunca commite o `.env` — ele já está no `.gitignore`):
   ```
   cp .env.example .env
   ```

4. **Rodar as migrations**
   ```
   flask db upgrade
   ```

5. **Rodar o seed inicial — obrigatório em instalação nova**

   A tabela de perfis é um catálogo fixo do sistema e só é populada por este script (não existe CRUD de perfil pela web). Sem rodá-lo, telas como `/admin/perfis` aparecem vazias e a edição de usuário fica sem checkboxes de perfil — não é bug, é dado faltando (ver seção 10.12 do BLUEPRINT.md).
   ```
   python seeds/seed_inicial.py
   ```
   O script imprime login e senha temporária do usuário `superadmin` no console. **O sistema força a troca dessa senha no primeiro login** — nenhuma tela fica acessível até ela ser trocada. Idempotente: rodar de novo não recria nem reseta o que já existe.

6. **Subir o servidor**
   ```
   python run.py
   ```
   Acesse `http://localhost:5000`.

## Rodando os testes

Não usa pytest — cada arquivo em `tests/` é um script standalone com asserts, executado via `-m`:
```
python -m tests.test_regressao_<nome>
```
Para rodar a suíte inteira (Bash):
```
for f in tests/test_regressao_*.py; do
  python -m "tests.$(basename "$f" .py)" || echo "FALHOU: $f"
done
```

## Instalação em produção (Linux Ubuntu/Debian)

As instruções abaixo assumem um servidor Ubuntu/Debian limpo e usam `/var/www/gestfila` como diretório da aplicação e `gestfila` como nome do serviço systemd. Sem domínio público (acesso só por IP interno) — o SSL usa certificado autoassinado, não Let's Encrypt.

> O repositório também tem um deploy de referência anterior em [`deploy/`](deploy/) (nome antigo `painel-senhas`, pré-rebrand — seção 10.24 do BLUEPRINT.md), com `gunicorn.conf.py`/vhost Apache já ajustados para conexões WebSocket de TV de longa duração e `X-Forwarded-Proto`. Os passos abaixo são um guia alternativo, mais enxuto, para provisionar um servidor do zero — confira `deploy/` para o timeout/proxy já validado em produção antes de divergir.

1. **Atualizar o sistema**
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

2. **Instalar dependências (Python, MySQL, Apache)**
   ```bash
   sudo apt install -y python3 python3-venv python3-pip mysql-server apache2 git openssl build-essential libmysqlclient-dev pkg-config
   sudo a2enmod proxy proxy_http proxy_wstunnel ssl headers rewrite
   ```

3. **Sincronização de horário**

   Várias correções do sistema (reset diário de senha, encerramento automático, relatórios por data) dependem do relógio do servidor estar certo — sem isso, esses bugs voltam a acontecer sozinhos.
   ```bash
   sudo apt install -y chrony
   sudo systemctl enable --now chrony
   chronyc tracking
   ```

4. **Clonar o repositório do GitHub**
   ```bash
   sudo mkdir -p /var/www/gestfila
   sudo chown $USER:$USER /var/www/gestfila
   git clone https://github.com/lucascardoso-lab/gestfila.git /var/www/gestfila
   cd /var/www/gestfila
   ```

5. **Criar ambiente virtual e instalar `requirements.txt`**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

   Exportação de relatórios em PDF (adendo 10.67): instale o Chromium do
   Playwright com `--with-deps` (também instala as bibliotecas de sistema que
   ele precisa — libnss3, libatk etc. — um Ubuntu limpo não vem com elas).
   `sudo` sozinho reseta o `PATH` e perderia o venv ativado acima — chame o
   `python` de DENTRO do venv explicitamente, não o `playwright` do PATH:
   ```bash
   sudo venv/bin/python -m playwright install --with-deps chromium
   ```

6. **Criar o banco de dados**
   ```bash
   sudo mysql -u root -p
   ```
   ```sql
   CREATE DATABASE gestfila CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'gestfila'@'localhost' IDENTIFIED BY 'SUA_SENHA_FORTE';
   GRANT ALL PRIVILEGES ON gestfila.* TO 'gestfila'@'localhost';
   FLUSH PRIVILEGES;
   EXIT;
   ```

7. **Configurar o `.env`**
   ```bash
   cp .env.example .env
   nano .env
   ```
   Preencha `SECRET_KEY` (gere com o comando abaixo), as credenciais do MySQL do passo 6, o ambiente de produção e o fuso horário da clínica.
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

8. **Rodar migrations e o seed inicial**
   ```bash
   flask db upgrade
   python seeds/seed_inicial.py
   ```
   O script imprime login e senha temporária do `superadmin` no console — anote antes de fechar o terminal. É idempotente, pode rodar de novo sem perder dado.

9. **Criar o serviço systemd (Gunicorn)**
   ```bash
   sudo nano /etc/systemd/system/gestfila.service
   ```
   ```ini
   [Unit]
   Description=GESTFILA
   After=network.target mysql.service

   [Service]
   User=www-data
   Group=www-data
   WorkingDirectory=/var/www/gestfila
   Environment="PATH=/var/www/gestfila/venv/bin"
   ExecStart=/var/www/gestfila/venv/bin/gunicorn --workers 1 --worker-class eventlet --bind 127.0.0.1:8000 --timeout 120 run:app
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
   `--workers 1` não é ajustável à toa aqui: o Flask-SocketIO guarda em memória, por processo, a lista de clientes conectados e as salas (`tv_<id>`/`fila_<id>`) — com mais de um worker, eventos emitidos a partir de um worker nunca chegam a um cliente conectado em outro (ver `deploy/gunicorn.conf.py` para o detalhamento e o caminho para escalar com Redis como `message_queue`, se algum dia for necessário).
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now gestfila
   sudo systemctl status gestfila
   ```

10. **Gerar o certificado SSL autoassinado**
    ```bash
    sudo mkdir -p /etc/ssl/gestfila
    sudo openssl req -x509 -nodes -days 825 \
      -newkey rsa:2048 \
      -keyout /etc/ssl/gestfila/gestfila.key \
      -out /etc/ssl/gestfila/gestfila.crt \
      -subj "/C=BR/ST=SEU_ESTADO/L=SUA_CIDADE/O=GESTFILA/CN=SEU_IP_INTERNO"
    ```
    Sem domínio, o navegador vai mostrar aviso de "conexão não segura" na primeira vez em cada dispositivo — normal nesse cenário. Anote a data de expiração (825 dias) para renovar manualmente.

11. **Configurar o Apache (proxy reverso + SSL)**

    `SEU_IP_INTERNO` aparece **3 vezes** neste bloco (as duas linhas `ServerName` + a linha `Redirect permanent`) — troque as 3 antes de salvar. Esquecer a linha `Redirect` especificamente quebra o redirecionamento HTTP→HTTPS **em silêncio** (sem erro no `apache2ctl configtest`, o navegador só "não redireciona") — já aconteceu numa instalação real.
    ```bash
    sudo a2dissite 000-default
    sudo nano /etc/apache2/sites-available/gestfila-ssl.conf
    ```
    ```apache
    <VirtualHost *:80>
        ServerName SEU_IP_INTERNO
        Redirect permanent / https://SEU_IP_INTERNO/
    </VirtualHost>

    <VirtualHost *:443>
        ServerName SEU_IP_INTERNO

        SSLEngine on
        SSLCertificateFile /etc/ssl/gestfila/gestfila.crt
        SSLCertificateKeyFile /etc/ssl/gestfila/gestfila.key

        Header always set X-Content-Type-Options "nosniff"
        Header always set X-Frame-Options "SAMEORIGIN"
        Header always set Strict-Transport-Security "max-age=63072000"

        ProxyPreserveHost On
        # Sem isso o Flask-SocketIO vê a conexão como HTTP (o Gunicorn nunca sabe que
        # o Apache está terminando TLS) e rejeita o handshake por origem inválida -
        # sintoma: painel de TV preso em "Reconectando..." pra sempre (adendo 10.61).
        RequestHeader set X-Forwarded-Proto "https"

        ProxyPass /socket.io/ ws://127.0.0.1:8000/socket.io/
        ProxyPassReverse /socket.io/ ws://127.0.0.1:8000/socket.io/
        ProxyPass / http://127.0.0.1:8000/
        ProxyPassReverse / http://127.0.0.1:8000/

        RewriteEngine On
        RewriteCond %{HTTP:Upgrade} =websocket [NC]
        RewriteRule /(.*) ws://127.0.0.1:8000/$1 [P,L]

        ErrorLog ${APACHE_LOG_DIR}/gestfila_error.log
        CustomLog ${APACHE_LOG_DIR}/gestfila_access.log combined
    </VirtualHost>
    ```
    ```bash
    sudo a2ensite gestfila-ssl
    sudo apache2ctl configtest
    sudo systemctl restart apache2
    ```

12. **Firewall**
    ```bash
    sudo apt install -y ufw
    sudo ufw allow OpenSSH
    sudo ufw allow 80/tcp
    sudo ufw allow 443/tcp
    sudo ufw enable
    ```

13. **Backup diário**
    ```bash
    sudo mkdir -p /opt/gestfila-backups
    sudo nano /etc/cron.daily/gestfila-backup
    ```
    ```bash
    #!/bin/bash
    DATA=$(date +%Y-%m-%d)
    mysqldump -u gestfila -p'SUA_SENHA_FORTE' gestfila | gzip > /opt/gestfila-backups/gestfila_$DATA.sql.gz
    find /opt/gestfila-backups -name "gestfila_*.sql.gz" -mtime +30 -delete
    ```
    ```bash
    sudo chmod +x /etc/cron.daily/gestfila-backup
    ```
    Copie os backups periodicamente para fora do servidor — dado de paciente (LGPD) não deveria depender de um único disco.

14. **Verificação final**
    - `https://SEU_IP_INTERNO` abre a tela de login
    - Login do `superadmin` força a troca de senha
    - Um totem gera uma senha de teste
    - Um painel de TV recebe a chamada em tempo real (confirma WebSocket)
    - `sudo ufw status` mostra só as portas esperadas

## Dado sensível — LGPD

Este sistema trata dado pessoal de paciente (nome, em campo de texto livre — seção 8 do BLUEPRINT.md) mesmo sem cadastro formal de paciente. **Nunca commite**:
- dumps de banco (`.sql`), arquivos `.db`/`.sqlite3`
- capturas de tela, logs ou fixtures de teste com dado real (nome, telefone, etc.)
- o arquivo `.env` ou qualquer credencial real

Se precisar reproduzir um bug com dado de produção, use dado sintético equivalente nos testes/documentação — nunca copie o valor real.

## Licença

Software proprietário de uso interno — todos os direitos reservados. Sem
licença de código aberto; não redistribuir nem reutilizar fora do contexto
desta clínica sem autorização.
