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
| Relatórios/export | pandas + openpyxl |

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
