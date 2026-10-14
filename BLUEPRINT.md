# BLUEPRINT — Sistema de Gestão de Senhas e Chamadas (multi-clínica)

> Documento de especificação para guiar o desenvolvimento incremental no Claude Code / VS Code.
> Stack definida: **Python 3.11+ / Flask / Flask-SocketIO / MySQL 8 / Apache2 (mod_wsgi ou proxy reverso) / Nginx opcional como proxy do WS**.
> Referência visual: sistema "ChameFácil" (Prolins) usado atualmente na clínica — telas de totem, painel de chamada (TV) e tela de alocação de fila foram usadas como inspiração de UX.

---

## 0. Como usar este documento com o Claude Code

Cole este arquivo na raiz do projeto como `BLUEPRINT.md` e peça ao Claude Code para:
1. Ler o blueprint inteiro antes de gerar qualquer código.
2. Implementar **por fases** (seção 9), nunca o sistema todo de uma vez.
3. Seguir o modelo de dados da seção 4 sem renomear tabelas/campos (evita retrabalho de migração).
4. Sempre perguntar antes de pular uma fase.

Prompt sugerido de abertura no Claude Code:
```
Leia o arquivo BLUEPRINT.md inteiro. Vamos implementar a Fase 1 (estrutura base +
autenticação + multi-tenant). Crie a estrutura de pastas, requirements.txt,
config e os models do SQLAlchemy. Não implemente totem/painel ainda.
```

---

## 1. Visão geral do produto

Sistema de **gerenciamento de filas e senhas de atendimento** para clínicas/hospitais, com:

- **Totem touch-screen**: paciente toca um botão de serviço → sistema gera senha → imprime (ou exibe) → senha entra na fila daquele serviço/local.
- **Painel de chamada (TV)**: exibe lista de senhas chamadas (local + senha), toca áudio de chamada (texto-para-voz ou pré-gravado), atualiza em tempo real via WebSocket.
- **Chamada por nome (sala de coleta)**: fluxo de 2 etapas —
  1. Operador chama a **senha** (ex.: `BR009`) no totem/TV, paciente se dirige à sala de coleta.
  2. Dentro da sala, o operador escreve o **nome do paciente em um campo livre** (sem cadastro prévio) e dispara uma **segunda chamada, agora pelo nome**, exibida na TV/painel.
- **Perfis de acesso**: Administrador, Gerencial (relatórios), Recepção, Coleta, Recepção+Coleta.
- **Multiempresa (multi-tenant)**: uma instalação só, múltiplas empresas/clínicas, cada uma com suas unidades (locais), usuários, filas e TVs.
- **Permissão por unidade**: usuário vinculado a uma ou mais unidades dentro da empresa.
- **Administração via web**: criar botões do totem, criar TVs, criar filas internas — tudo sem precisar editar código.
- **Relatórios**: tempo de atendimento por serviço, por operador (coletador/recepção), por setor, por local/unidade.

---

## 2. Padrão da senha impressa (regra de negócio crítica)

Senha = **prefixo (sigla do serviço) + sequencial numérico**, reiniciando por dia (e por unidade).

Exemplos observados nas imagens: `BR009` (Broncoscopia), `A N294` / `AN294` (Atendimento Normal).

Regras a implementar:
- Cada **serviço** (ex.: Laboratório, Ultrassonografia, Quimioterapia, Consultórios, Coleta, Broncoscopia) tem uma **sigla configurável** (2–4 letras), definida pelo admin ao criar o botão do totem.
- Sequencial: `0001`–`9999`, **zera à meia-noite** (configurável: por dia ou nunca zera).
- Sequencial é **por unidade + por serviço** (duas unidades diferentes podem ter `A001` ao mesmo tempo sem conflito).
- Formato de exibição: `{SIGLA}{NUMERO:03d}` (ajustável: 3 dígitos como no exemplo `BR009`, ou outro padding configurável por empresa).
- Prioridade: senhas de **prioridade** (idoso, gestante, etc.) usam sigla própria (ex.: `P` antes do número, ou fila com peso/prioridade — ver tela "Alocar fila" com "Quantidade de chamadas seguidas", que define quantas senhas prioritárias avançadas seguidamente antes de voltar à fila normal — replicar esse comportamento de **rodízio configurável**).

Tabela de regras por serviço fica no model `Servico` (seção 4).

---

## 3. Perfis de acesso (roles)

| Perfil | Permissões |
|---|---|
| **Super Admin** (dono do sistema) | Cria empresas, vê tudo, não pertence a nenhuma empresa específica |
| **Administrador (empresa)** | CRUD completo dentro da própria empresa: unidades, usuários, botões do totem, TVs, filas, serviços, siglas. Acesso a todos os relatórios de todas as unidades da empresa |
| **Gerencial** | Somente leitura de relatórios (pode ser restrito a unidades específicas) |
| **Recepção** | Opera o totem/gera senha manualmente, chama senha no painel, NÃO acessa coleta nem relatórios |
| **Coleta** | Chama senha + tela de "chamar por nome", NÃO acessa recepção nem relatórios |
| **Recepção + Coleta** | União dos dois perfis acima |

Implementar como **RBAC simples**: tabela `perfis` com permissões em JSON ou tabela `perfil_permissoes`, associada a `usuario` via `usuario_empresa_unidade` (ver seção 4) — isso cobre o requisito de "permissão por unidade".

---

## 4. Modelo de dados (MySQL — multi-tenant via `empresa_id`)

```
empresas
  id PK
  nome
  cnpj
  ativo
  criado_em

unidades                         -- "locais" (Recepção, Coleta, Consultório 3º andar, etc.)
  id PK
  empresa_id FK -> empresas
  nome
  ativo

usuarios
  id PK
  empresa_id FK -> empresas      -- NULL se for super admin
  nome
  login
  senha_hash
  ativo
  criado_em

perfis
  id PK
  nome                           -- 'admin', 'gerencial', 'recepcao', 'coleta', 'recepcao_coleta'
  descricao

usuario_perfil
  usuario_id FK
  perfil_id FK

usuario_unidade                  -- permissão por unidade
  usuario_id FK
  unidade_id FK

servicos                          -- ex: Laboratório, Ultrassonografia, Coleta, Broncoscopia
  id PK
  empresa_id FK
  unidade_id FK
  nome
  sigla              VARCHAR(4)  -- usada na senha, ex 'BR', 'A'
  digitos_sequencial  TINYINT DEFAULT 3
  zera_diariamente    BOOLEAN DEFAULT TRUE
  prioridade_padrao   TINYINT DEFAULT 0
  ativo

totem_botoes                      -- botões configuráveis do totem (telas e submenus)
  id PK
  empresa_id FK
  unidade_id FK
  parent_id FK -> totem_botoes (NULL = botão de tela raiz, permite submenu como na Imagem 2/3)
  servico_id FK NULL               -- se preenchido, botão gera senha direto
  label                            -- texto exibido, ex 'ULTRASSONOGRAFIA'
  ordem
  ativo

filas
  id PK
  empresa_id FK
  unidade_id FK
  servico_id FK
  nome
  prioridade_rodizio  TINYINT DEFAULT 0  -- "quantidade de chamadas seguidas" prioritárias (Imagem 1)
  ativo

senhas
  id PK
  empresa_id FK
  unidade_id FK
  servico_id FK
  fila_id FK
  codigo              VARCHAR(10)   -- ex: 'BR009'
  sequencial           INT
  prioridade           BOOLEAN DEFAULT FALSE
  status               ENUM('aguardando','chamada','em_atendimento','finalizada','cancelada')
  nome_paciente        VARCHAR(120) NULL   -- preenchido só na hora da chamada por nome, sem cadastro
  guiche_local         VARCHAR(60) NULL    -- ex: 'Coleta A 01', 'Guichê 02'
  criada_em            DATETIME
  chamada_em           DATETIME NULL
  chamada_nome_em      DATETIME NULL       -- 2ª chamada (por nome, na coleta)
  atendimento_iniciado_em DATETIME NULL
  finalizada_em        DATETIME NULL
  operador_id FK -> usuarios NULL
  setor                ENUM('recepcao','coleta','outro')

tvs                                -- TVs cadastráveis pelo admin (Imagem 5)
  id PK
  empresa_id FK
  unidade_id FK
  nome                 -- 'TV 1º Andar', 'TV Mesanino 01', etc.
  layout               -- referência a um template visual (opcional, fase 2)
  ativo

tv_filas                            -- quais filas cada TV exibe
  tv_id FK
  fila_id FK

logs_atendimento                    -- base para relatórios de tempo
  id PK
  senha_id FK
  evento     ENUM('gerada','chamada_senha','chamada_nome','inicio_atendimento','fim_atendimento')
  timestamp
  usuario_id FK NULL
  unidade_id FK
  setor ENUM('recepcao','coleta','outro')
```

**Índices recomendados:** `(empresa_id, unidade_id, status)` em `senhas`; `(servico_id, criada_em)` para o cálculo do sequencial diário.

---

## 5. Fluxos principais

### 5.1 Geração de senha (Totem)
1. Paciente toca botão (pode haver navegação em submenu — ver Imagens 2 e 3: tela raiz → categoria → médico/serviço específico).
2. Backend calcula próximo sequencial do serviço/unidade/dia, monta `codigo`.
3. Grava em `senhas` (status `aguardando`), grava log `gerada`.
4. Emite evento WebSocket para a fila (atualiza contadores) — TV não exibe ainda, só quando for chamada.
5. Exibe/imprime o código ao paciente.

### 5.2 Chamada da senha (painel/TV)
1. Operador (recepção ou coleta) clica "Chamar próxima" ou seleciona senha específica + define `guiche_local`.
2. Status → `chamada`, grava `chamada_em`, log `chamada_senha`.
3. Emite WebSocket (`namespace /painel`, sala = `tv_<id>` ou `fila_<id>`) com payload `{local, senha}`.
4. TV recebe e:
   - Insere no topo da lista (linha destacada em vermelho, como na Imagem 4).
   - Dispara áudio (TTS ou arquivo de voz: "Senha BR009, dirija-se à Coleta A 01").
5. Regra de rodízio de prioridade (campo `prioridade_rodizio` da fila) decide se a próxima chamada automática deve ser prioritária ou normal.

### 5.3 Chamada por nome (sala de coleta) — fluxo de 2 etapas
1. A senha já foi chamada (5.2) e o paciente está na sala.
2. Operador da coleta abre tela "Atendimento Interno"/"Coleta", digita o nome **em campo livre** (sem buscar/cadastrar paciente).
3. Sistema grava `nome_paciente`, `chamada_nome_em`, log `chamada_nome`.
4. Emite WebSocket separado (ou mesmo canal, payload com `tipo: 'nome'`) para a TV da sala de coleta exibir o nome chamado.
5. Esse nome **não fica salvo como cadastro reutilizável** — é só texto livre por chamada (atende ao requisito "sem necessidade de cadastro do nome do paciente").

### 5.4 Tela "Alocar fila" (Imagem 1) — replicar
Tela administrativa/operacional onde o operador:
- Seleciona **Prioridade** (nível) + **quantidade de chamadas seguidas** daquele nível antes de voltar ao rodízio normal.
- Seleciona **Local** (unidade) onde a fila será alocada.
- Botão "Adicionar prioridade" empilha regras (mostradas na tabela à direita: Prioridade × Chamadas).
- Botão "Alocar" salva a configuração de rodízio para aquela fila/unidade.

### 5.5 Cadastro de botões do totem (admin web) — replicar Imagens 2 e 3
- CRUD de `totem_botoes` com suporte a hierarquia (botão pode abrir submenu de outros botões, como `CONSULTÓRIOS` abrindo lista de médicos).
- Cada botão final (sem filhos) associa a um `servico_id` (gera senha) OU pode ser puramente navegacional (sem `servico_id`, só abre submenu).
- Admin define label, ordem de exibição, unidade de destino.

### 5.6 Cadastro de TVs (admin web) — replicar Imagem 5
- CRUD de `tvs`: nome, unidade, quais filas aparecem nela (`tv_filas`).
- Sistema gera uma URL única por TV (ex.: `/painel/tv/<id>`) para abrir no navegador da Smart TV.

---

## 6. Painel de chamada (TV) — requisitos técnicos

- Página HTML simples, full-screen, sem necessidade de login (acesso por URL fixa/token).
- Conecta via **Socket.IO client** (JS) à sala correspondente à TV.
- Layout: 2 colunas (Local | Senha) como na Imagem 4, linha mais recente destacada, lista das últimas N chamadas, rodapé com indicadores (ex.: tempo médio de espera).
- Áudio: usar **Web Speech API (`speechSynthesis`)** no navegador da própria Smart TV (evita depender de arquivos de áudio prontos) — fallback para arquivos `.mp3` pré-gravados se a Smart TV não suportar TTS bem em PT-BR.
- Deve sobreviver a reconexões (Smart TVs ficam dias ligadas) — implementar `reconnection: true` no Socket.IO client e um *heartbeat*/sala persistente no servidor.

---

## 7. Relatórios (perfil Gerencial/Admin)

Baseados na tabela `logs_atendimento`, calcular:

1. **Tempo médio de espera** (gerada → chamada_senha), por serviço, por unidade, por dia/período.
2. **Tempo de atendimento** (chamada_senha → fim_atendimento, ou chamada_nome → fim_atendimento na coleta), por operador, por setor (recepção/coleta), por serviço.
3. **Volume de senhas** por serviço/unidade/período, comparativo prioridade vs. normal.
4. **Ranking de operadores** (coletador/recepcionista) por volume e tempo médio.
5. Exportação CSV/Excel (usar `openpyxl` ou `pandas`).
6. Filtros: empresa (se super admin), unidade, serviço, operador, intervalo de datas.

Sugestão de implementação: views agregadas (`CREATE VIEW`) ou queries com `GROUP BY` direto via SQLAlchemy — não precisa de data warehouse para o volume esperado.

**Tempo de atendimento — todos os segmentos de uma senha realocada (adendo
10.62):** o relatório 2 mede CADA segmento de atendimento de uma senha (ex.:
Recepção → Fila A → Fila B → Finalizado = 3 segmentos), via
`_segmentos_intermediarios_realocacao` (`app/relatorios/queries.py`) para os
segmentos intermediários, mais o último segmento já coberto (última chamada →
`fim_atendimento`, `_pares_inicio_fim`, sem mudança). Cada segmento traz a
fila/local reais em que ocorreu, a partir de um snapshot gravado no próprio
evento (`LogAtendimento.fila_id`/`local_id`) - **exceto para dados gravados
antes desta mudança**, que continuam exibidos exatamente como antes (só o
primeiro segmento, sem fila/local, "—" no lugar) - decisão de produto
deliberada, não uma lacuna a corrigir; ver adendo 10.62 para o detalhe
completo (era só o PRIMEIRO e o último segmento que eram medidos, e sem
fila/local para o primeiro - limitação anterior, já superada para dado novo).

**Correção deliberada — Realocar não grava mais log `chamada_nome`:** o botão
Realocar (seção 10.2) permite editar o campo Nome no mesmo formulário usado
para mover a senha para outra fila. Antes, essa edição de nome gravava um log
`chamada_nome` normal — como esse log fica cronologicamente colado ao evento
`realocado` (mesma request), `_pares_inicio_realocado` o escolhia como "início"
do segmento em vez do `chamada_senha` genuíno que de fato abriu o atendimento
na fila de origem, medindo esse segmento como ~0s em vez do tempo real. Desde
a correção, `talvez_atualizar_nome()` (`app/atendimento/helpers.py`) aceita
`registrar_como_chamada=False` - usado só pelo Realocar - que continua
gravando `nome_paciente`/`chamada_nome_em` na senha (estado normal), mas não
cria esse log. Como efeito colateral **também deliberado** (não é um bug à
parte): essa edição de nome no Realocar deixa de contar como uma chamada em
`ranking_operadores()` (volume) e no feed de `ultimas_chamadas()` do
dashboard — antes, uma única visita "Chamar → Realocar com nome" inflava
esses dois relatórios contando 2 chamadas para 1 atendimento físico; agora
conta 1, como deveria. O evento `realocado` (não afetado, sem mudança)
continua sendo o marcador de auditoria do hand-off em si.

---

## 8. Boas práticas de sistemas de senha (a aplicar)

- **Idempotência na geração de senha**: usar transação + `SELECT ... FOR UPDATE` na tabela de sequencial por serviço/unidade/dia para evitar senha duplicada em concorrência (totem é tocado por múltiplos pacientes simultaneamente).
- **Separar geração de evento de notificação**: gravar no banco primeiro, emitir WebSocket depois (garante consistência mesmo se o front cair).
- **Idle/keep-alive nas TVs**: ping periódico + auto-reload diário (Smart TVs acumulam memory leak em sessões de navegador de dias).
- **Modo de contingência manual**: permitir chamar senha digitando o código manualmente, caso o totem físico falhe.
- **Auditoria**: nunca apagar `senhas`/`logs_atendimento` — usar `status = 'cancelada'`, nunca `DELETE`.
- **Sigla de serviço única por unidade** (constraint), evita colisão de código de senha.
- **Reset de sequencial à meia-noite** via job agendado (ou cálculo *on the fly* filtrando `criada_em::date = hoje`, o que evita depender de cron).
- **Multi-tenant**: todo o middleware de autenticação deve injetar `empresa_id` no contexto da requisição e **toda query deve filtrar por ele** — nunca confiar em filtro feito só no front.
- **Senhas de usuário (login)**: hash com `werkzeug.security` (`generate_password_hash`/`bcrypt`), nunca texto puro.
- **Rate limit no endpoint de geração de senha do totem** (evita "ataque" de toque repetido gerando milhares de senhas).
- **Data/hora no fuso da clínica (regra explícita desde o adendo 10.74):** colunas de data/hora são gravadas em UTC "naive" (`datetime.utcnow`). Hora/data exibida no servidor **sempre** via `para_horario_clinica()` ou o filtro Jinja `hora_clinica` (`app/utils/tempo.py`); agrupar "por dia" = `para_horario_clinica(coluna).date()`; filtrar "hoje"/um dia = `janela_dia_clinica_em_utc()` (senhas `aguardando` listadas/contadas/chamadas: sempre com `criada_hoje_na_clinica()`, 10.77); "agora" = `agora_na_clinica()`. **Nunca** `strftime`/`.date()`/`.hour` direto sobre coluna UTC, nem `datetime.now()`/`datetime.utcnow().date()` para decidir o dia. Datetime enviado ao JS: ISO + `"Z"` (o navegador converte).
- **Acessibilidade**: tela do totem com fontes grandes, alto contraste — já presente no design de referência (botões grandes, cores fortes).
- **LGPD**: nome do paciente é dado pessoal mesmo sendo "campo livre sem cadastro" — definir política de retenção/expurgo desse campo após X dias.

---

## 9. Fases de implementação sugeridas (para o Claude Code)

| Fase | Entrega |
|---|---|
| **1** | Estrutura do projeto, `requirements.txt`, config Flask, conexão MySQL (SQLAlchemy), models da seção 4, autenticação (login/logout, hash de senha), middleware multi-tenant |
| **2** | CRUD admin: empresas, unidades, usuários, perfis, vínculo usuário-unidade |
| **3** | CRUD admin: serviços (siglas), botões do totem (com hierarquia), filas, TVs |
| **4** | Totem: tela pública de seleção de serviço + geração de senha (com lock de concorrência) |
| **5** | WebSocket (Flask-SocketIO): infraestrutura de salas por TV/fila |
| **6** | Painel de chamada (TV): front-end + integração WS + TTS |
| **7** | Tela de chamada da senha (recepção/coleta) + tela "Alocar fila" (rodízio de prioridade) |
| **8** | Fluxo de chamada por nome (coleta) — 2ª etapa |
| **9** | Relatórios + exportação |
| **10** | Hardening: rate limit, logs de auditoria, testes, deploy Apache2/Gunicorn |

**Recomendação de execução:** uma fase por sessão de chat no Claude Code, validando cada fase antes de avançar.

---

## 10. Stack técnica resumida

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.11+, Flask, Flask-SQLAlchemy, Flask-Login, Flask-SocketIO (com `eventlet` ou `gevent`) |
| Banco | MySQL 8 (driver `PyMySQL` ou `mysqlclient`) |
| Front (totem/painel/admin) | Jinja2 + JS puro (ou HTMX para interações sem SPA) + Socket.IO client |
| Servidor de produção | Gunicorn + `eventlet` worker, atrás de **Apache2** (`mod_proxy`/`mod_proxy_wstunnel`) fazendo proxy para o Gunicorn — Apache2 puro com `mod_wsgi` **não sustenta WebSocket bem**, por isso o proxy reverso é necessário |
| Relatórios/export | pandas + openpyxl |
| Deploy alvo | Linux (Ubuntu/Debian), systemd service para o Gunicorn |

---

## 10.0. ÍNDICE DOS ADENDOS (10.1+)

> Os adendos abaixo registram, em ordem cronológica, cada decisão/mudança
> feita depois do escopo original (seções 1–10). Um adendo mais novo pode
> superseder parte de um mais antigo - quando isso acontece, há nota cruzada
> nos dois. Numeração sequencial, sem lacunas.

| Nº | Título |
|---|---|
| 10.1 | Fusão de Serviço em Fila |
| 10.2 | Tela unificada de Atendimento (chamar/tocar/realocar/finalizar + redirecionamento) |
| 10.3 | Cadastro de Locais (guichês/postos) |
| 10.4 | Tipos de senha configuráveis (prefixo/cor/peso) |
| 10.5 | Navegação multi-nível do totem + quantidade de acompanhantes *(acompanhantes removido, ver 10.34)* |
| 10.6 | Impressão física via térmica Bematech (ESC/POS) *(supersedida pela 10.28)* |
| 10.7 | Link direto de acesso público (Totens e TVs) |
| 10.8 | Rodízio de chamadas por Local (múltiplas filas) |
| 10.9 | Super admin global criável pelo painel *(senha temporária + troca obrigatória, ver 10.60)* |
| 10.10 | Busca com autocomplete no bloco Redirecionar |
| 10.11 | Horário de relógio junto da duração (Atendimento) |
| 10.12 | Nota operacional — seed de Perfis obrigatório |
| 10.12.2 | Dashboard operacional pós-login (KPIs, gráfico, filas, últimos atendimentos) |
| 10.13 | Correções no painel/TV (duplicação, histórico, voz) |
| 10.14 | Exclusão segura (Excluir) nas telas administrativas |
| 10.15 | Ocupação de locais (um usuário por Local) + logout remoto |
| 10.16 | Decisão de UX — botão "Chamar" oculto para perfis operacionais |
| 10.17 | Bloqueio de chamada sem finalização + recuperação de estado |
| 10.18 | Encerramento automático das sessões de atendimento (23h) |
| 10.19 | Tela "Iniciar Turno" (configuração de turno pelo operador) |
| 10.20 | "Minhas filas" (acesso rápido) no bloco Redirecionar *(removido, ver 10.29)* |
| 10.21 | Renome dos perfis recepcao/coleta → atendente/executor |
| 10.22 | UX de contexto multi-unidade no painel admin |
| 10.23 | "Iniciar Turno" padronizada como tela de todo login |
| 10.24 | Identidade visual GESTFILA (rebrand completo) |
| 10.25 | Hardening de segurança (CSRF, rate limit, proxy, páginas de erro) |
| 10.26 | Redesign do painel de chamada (TV): card central, identidade, animação |
| 10.27 | Correções pós-homologação do redesign do painel de TV |
| 10.28 | Impressão do totem via window.print() do navegador *(substitui a 10.6)* |
| 10.29 | Remoção de "Minhas filas" no bloco Redirecionar *(supersede 10.20)* |
| 10.30 | Escala responsiva de "Últimas chamadas" e rodapé no painel de TV |
| 10.31 | Rótulos "Atendente"/"Executor" em `Local.tipo` e telas de setor |
| 10.32 | Separação `Local.nome` em `nome_exibicao`/`identificador_admin` |
| 10.33 | Fallback de áudio pré-gravado no painel de TV (Web Speech API quebrada em Smart TV) |
| 10.34 | Remoção da etapa "Quantidade de acompanhantes" no totem *(supersede parte da 10.5)* |
| 10.35 | Super admin com senha aleatória + troca obrigatória no 1º login |
| 10.36 | Integração contínua (GitHub Actions) + compat Linux do `win32print` |
| 10.37 | Fuso da clínica resolvido no código (encerramento/filas corretos em servidor UTC) |
| 10.38 | Correção do layout do cupom impresso (centralização + contraste) + procedimento de impressão |
| 10.39 | Reset diário do sequencial usava dia civil em UTC, não da clínica (3º call-site do bug da 10.37) |
| 10.40 | Etiqueta física de tamanho fixo (7x8cm): `@page` altura auto + páginas extras em branco *(premissa física revista, ver 10.44)* |
| 10.41 | Single-session enforcement no login (reaproveita sessao_versao da 10.15) |
| 10.42 | Investigação "impressão em paisagem" - código correto, suspeita de driver Windows (sem mudança de código) |
| 10.43 | Upload de logo por empresa (cupom + painel de TV + admin) |
| 10.44 | `@page` volta pra altura "auto" - premissa da 10.40 estava errada (rolo contínuo, não etiqueta fixa) |
| 10.45 | Campos opcionais nulos apareciam como a string "None" (cupom mostrando "None"/"None") |
| 10.46 | Impressão silenciosa (kiosk) saía sem CSS: `<script>` (window.print síncrono) vinha antes do `<style>` do cupom no HTML |
| 10.47 | Sigla passa de `Fila` para `TotemBotao` (realinha com a seção 2 original) |
| 10.48 | Corte de início da fala no painel de TV em hardware fraco (Celeron + 4GB) |
| 10.49 | Corte de início da fala no painel de TV: causa DIFERENTE da 10.48 (atraso de warm-up do motor de TTS, não CPU) |
| 10.50 | EXPERIMENTAL: heartbeat periódico do motor de speechSynthesis (corte de início após ociosidade, causa diferente da 10.48/10.49) |
| 10.51 | EXPERIMENTAL: "despertar" o hardware de saída de áudio via Web Audio API (quarta tentativa, causa diferente das anteriores) |
| 10.52 | Destaque visual por tempo de espera (escala de 3 níveis) na tabela "Senhas em espera" |
| 10.53 | Dois bugs independentes na tela "Editar local": checklist de filas fora de escopo + barra de contexto sem efeito em telas de editar |
| 10.54 | Filtro De/Até dos relatórios: calendário nativo + padrão "hoje" em vez de 30 dias |
| 10.55 | Consolidação pré-dark-mode: variáveis semânticas novas + `<nav>` unificado (etapa 1 de 2) |
| 10.56 | Dark mode em si: tema claro/escuro/automático em todo o projeto (etapa 2 de 2) |
| 10.57 | Persistência de filas do turno entre logout/login (reversão PARCIAL e DELIBERADA da seção 10.23) |
| 10.58 | Fila real de chamadas de voz no painel de TV (chamadas nunca se cortam) |
| 10.59 | 3 achados de uma instalação real em produção (Ubuntu 24 LTS, seguindo o README do zero) |
| 10.60 | Criação de super admin global via UI segue o mesmo padrão de senha do seed (temporária + troca obrigatória) |
| 10.61 | Painel de TV preso em "Reconectando..." em produção: faltava `X-Forwarded-Proto` no vhost do README (mesmo padrão da 10.59/10.60) |
| 10.62 | Tempo de atendimento mede TODOS os segmentos de uma senha realocada (não só o 1º e o último) |
| 10.63 | "Conclusão" vs. "Repasse": tempo_atendimento() misturava dois tipos de segmento na mesma linha (achado em revisão do 10.62) |
| 10.64 | "Botão pai" e "Fila" na tela de Botões do totem não filtravam por Unidade (mesma classe de bug do 10.53) |
| 10.65 | Totem esconde botões de fila fora do horário de funcionamento, atualizando sozinho sem F5 |
| 10.66 | "Tempo médio de espera" media só o 1º intervalo da jornada, não cada estágio (mesma classe de bug do 10.62/10.63, agora no relatório irmão) |
| 10.67 | Exportação de relatórios em PDF (complementar ao CSV/Excel existente) |
| 10.68 | Dois achados independentes na exportação de PDF (adendo 10.67): eventlet trava no Windows local + cache do Chromium por usuário em produção |
| 10.69 | Fila sem identificação de unidade nos relatórios/dashboards + select de Fila não filtrava pela Unidade escolhida |
| 10.70 | Rótulo de fila "[SIGLA] Nome" da função única do 10.69 nas telas operacionais e cadastros admin |
| 10.71 | Campo "Sigla" no cadastro de Unidade + atualização opcional do prefixo dos Locais |
| 10.72 | Cor dos links de conteúdo nos temas claro e escuro (correção na origem) |
| 10.73 | Relatório "Tempos por senha" (espera e atendimento de cada senha, para exportar) |
| 10.74 | Agrupamento "por dia" e "hoje" no dia civil da clínica (não de UTC) nos relatórios e no dashboard |
| 10.75 | Tempos exibidos em minutos e segundos (mm:ss / h:mm:ss), não em minuto decimal |
| 10.76 | "Áreas": agrupamento cadastrável de filas para os relatórios |
| 10.77 | Senhas abertas de dias anteriores: encerramento por dia da clínica + filtro de "hoje" em toda fila de espera |

---

## 10.1. ADENDO — Fusão de Serviço em Fila (decisão pós-Fase 10)

> Decisão tomada após testar o totem na prática: a separação Serviço/Fila criava um
> passo a mais sem ganho real (cada médico/serviço precisa de sua própria sigla e
> sequencial, e sempre acaba tendo exatamente 1 fila por serviço na prática).
> Inspirado na tela de referência do sistema concorrente (ChameFácil/Prolins).

**Mudança de modelo:** a tabela `servicos` é **eliminada**. Seus campos passam a
viver direto em `filas`:

```
filas
  id PK
  empresa_id FK
  unidade_id FK
  nome
  sigla              VARCHAR(4)        -- migrado de servicos.sigla
  digitos_sequencial  TINYINT DEFAULT 3 -- migrado de servicos.digitos_sequencial
  zera_diariamente    BOOLEAN DEFAULT TRUE -- migrado de servicos.zera_diariamente
  prioridade_padrao   TINYINT DEFAULT 0    -- migrado de servicos.prioridade_padrao
  prioridade_rodizio  TINYINT DEFAULT 0    -- já existia em filas
  totem_id FK -> tvs ou tabela própria de totens (NULL = disponível em todos)
  tv_id FK -> tvs (NULL = usa o vínculo via tv_filas existente; ver nota abaixo)
  horario_funcionamento JSON  -- ver estrutura abaixo
  tempo_espera_maximo_min  INT NULL
  tempo_atendimento_maximo_min INT NULL
  alerta_email             BOOLEAN DEFAULT FALSE
  ativo
```

**`horario_funcionamento` (JSON), estrutura sugerida:**
```json
{
  "domingo":  {"manha": false, "tarde": false},
  "segunda":  {"manha": true,  "tarde": true},
  "terca":    {"manha": true,  "tarde": true},
  "quarta":   {"manha": true,  "tarde": true},
  "quinta":   {"manha": true,  "tarde": true},
  "sexta":    {"manha": true,  "tarde": true},
  "sabado":   {"manha": true,  "tarde": false}
}
```
Fora do horário configurado, o totem deve recusar gerar senha para aquela fila
(mensagem similar à já existente "Este serviço ainda não está configurado...").

**Sobre `tv_id` direto na fila vs. tabela `tv_filas` existente:** manter `tv_filas`
(relação N:N) para o caso de uma fila aparecer em mais de uma TV, mas o campo
`tv_id`/`totem_id` simples na própria fila serve como **atalho de cadastro rápido**
(ao criar a fila, já vincula automaticamente a 1 TV e 1 totem, populando `tv_filas`
por trás) — replicar a UX do print de referência, que tem isso como dropdown simples.

**Todas as referências a `servico_id` no restante do documento** (totem_botoes,
senhas, logs_atendimento, relatórios da seção 7) passam a apontar para `fila_id`
diretamente — o conceito de "serviço" deixa de existir como entidade, e os botões
do totem passam a vincular direto a uma `fila_id`.

**Migração de dados existentes (script obrigatório, não apenas schema):**
1. Para cada linha em `servicos`, criar/atualizar a `fila` correspondente
   copiando sigla, digitos_sequencial, zera_diariamente, prioridade_padrao.
2. Atualizar `totem_botoes.servico_id` → `totem_botoes.fila_id` apontando para
   a fila migrada.
3. Atualizar `senhas.servico_id` → `senhas.fila_id` (já existia fila_id em
   senhas conforme seção 4 original — confirmar que bate com a fila migrada,
   não com uma fila nova).
4. Não fazer DROP da tabela `servicos` até confirmar que a migração de dados
   rodou sem perda (manter como tabela "morta" por 1 deploy, remover depois).

**Caso real que motivou a mudança:** um botão "MEDICOS" (menu) tinha sigla
genérica CM compartilhada por todos os médicos do submenu. Após a fusão, cada
médico (ex.: Rodrigo Brum, Leandro Gonçalves) deve ser sua própria `fila` com
sigla própria (RB, LG, etc.), vinculada a um botão-folha do totem; o botão
"MEDICOS" em si deve ficar **sem** fila vinculada (campo `fila_id` nulo no
botão), servindo apenas de menu de navegação para o submenu.

---

## 10.2. ADENDO — Tela unificada de Atendimento (chamar/tocar/realocar/finalizar + redirecionamento)

> Baseado em referência visual do sistema concorrente (ChameFácil/Prolins), tela
> "Atendimento". Substitui/expande a tela simples de "chamada de senha" descrita
> na seção 5.2 original por um painel operacional único, mais completo.

**Layout da tela (uma única tela por operador, por local fixo):**
- Campo **Local** — fixo para aquele posto de atendimento (ex.: "Coleta B 02 -
  Coleta Exames"), definido no perfil/sessão do operador ou escolhido ao logar.
- Campo **Senha chamada** — mostra a senha atualmente em atendimento naquele local.
- Campo **Agendados** — fila de pacientes com horário agendado (fora do escopo
  imediato; registrar como pendência futura, não implementar nesta fase).
- Campo **Código** — busca/chama uma senha específica digitando o código
  manualmente (modo de contingência, já previsto na seção 8 do blueprint original).
- Campo **Nome** — **é aqui que se registra o nome do paciente** (texto livre,
  sem cadastro), consolidando o que a seção 5.3 original descrevia como tela
  separada. Passa a fazer parte desta tela única.
- Bloco **Redirecionar** — lista de filas de destino (ex.: Coleta, ENTREGA DE
  AMOSTRA, PCD) como **radio buttons** (seleção única, não múltipla) — ao marcar
  uma e confirmar, a senha é movida para aquela fila/setor.
- Botões de ação: **Chamar | Tocar | Realocar | Finalizar | Aguardar | Enquetes**

**Semântica de cada botão de ação:**
| Botão | Efeito |
|---|---|
| **Chamar** | Avança para a próxima senha da fila (ou a do campo Código, se preenchido); status → `chamada`; grava `chamada_em`; emite WebSocket para a TV; dispara áudio |
| **Tocar** | Repete o áudio/exibição da senha **atualmente** chamada, sem alterar nenhum status ou timestamp — útil quando o paciente não ouviu/não apareceu |
| **Realocar** | Move a senha atual para **outra fila/local**, usando o bloco Redirecionar (radio button) como destino; equivale a trocar `fila_id`/`guiche_local` da senha em andamento, sem finalizar o atendimento |
| **Finalizar** | Conclui o atendimento; status → `finalizada`; grava `finalizada_em` (ou `atendimento_iniciado_em`→fim, conforme o fluxo já especificado na seção 4); libera o operador para chamar a próxima |
| **Aguardar** | Coloca a senha atual de volta em espera (status volta para `aguardando`), sem finalizar — para quando o atendimento precisa pausar |
| **Enquetes** | Fora de escopo por ora — registrar como pendência futura (possível pesquisa de satisfação pós-atendimento), não implementar nesta fase |

**Sobre o campo Nome + Redirecionar combinados:** o fluxo real observado é:
operador chama a senha (paciente chega), digita o nome no campo livre, e *então*
pode usar Realocar para mandar esse paciente (já identificado pelo nome) para a
fila de Coleta, por exemplo — unificando o que antes eram "2 chamadas separadas"
(seção 5.3 original) em uma única tela operacional contínua.

**Multi-tenant/RBAC:** esta tela é acessada pelos perfis `recepcao`, `coleta`,
`recepcao_coleta` e `admin`, restrita às filas/unidades permitidas ao operador,
seguindo o mesmo middleware já validado nas fases anteriores.

**Listas auxiliares na mesma tela (referência adicional: ver imagem mais completa
do fluxo em produção):** abaixo dos botões de ação, a tela exibe duas tabelas
em tempo real:

1. **"Senhas em espera"** — todas as senhas com status `aguardando` para a(s)
   fila(s) daquele local, com colunas:
   - **Senha** (código, ex.: `A N275`)
   - **T. Espera** — tempo ao vivo desde `criada_em` até agora, formato
     `HH:MM:SS`, recalculado continuamente no front (não precisa de novo
     evento de servidor por segundo; calcular client-side a partir do
     timestamp de criação recebido)
   - **H. Agend.** — horário agendado, se houver (mesmo "Agendados" do campo
     superior; exibir vazio quando não houver agendamento, sem bloquear o
     restante da funcionalidade)
   - **Nome** — preenchido apenas se o operador já tiver registrado nesta tela
   - **Observações** — texto livre opcional por senha (campo novo; adicionar
     `observacoes` em `senhas`, nullable)
   - **Ações** — botão "Chamar" individual por linha, que chama *aquela* senha
     específica diretamente (atalho equivalente a preencher o campo Código
     e clicar Chamar)

2. **"Senhas atendidas"** — histórico das últimas senhas já chamadas/finalizadas
   naquele local, com colunas:
   - **Senha**
   - **Tempo de espera** — tempo total entre `criada_em` e `chamada_em`
     (congelado, não ao vivo, já que o atendimento já ocorreu)
   - **Guichê** — o `guiche_local` usado naquele atendimento

Essas duas tabelas devem atualizar via WebSocket (mesma infraestrutura da Fase
5/6): ao chamar uma senha, ela some de "Senhas em espera" e aparece no topo de
"Senhas atendidas" em tempo real, sem precisar recarregar a página.

---

## 10.3. ADENDO — Cadastro de Locais (guichês/postos de atendimento)

> O campo "Local" da tela de Atendimento (seção 10.2) estava implementado como
> texto livre editável, o que gera inconsistência (o mesmo posto físico podendo
> ser digitado de formas diferentes por operadores diferentes, quebrando
> relatórios e agrupamentos). Vira uma entidade cadastrável própria.

**Nova tabela:**
```
locais
  id PK
  empresa_id FK
  unidade_id FK
  nome           -- ex: "rec1-01", "Coleta B 02", "Guichê Farmácia"
  tipo           ENUM('recepcao','coleta','outro') NULL  -- opcional, ajuda a
                                                            -- filtrar locais por
                                                            -- contexto de uso
  ativo
```

**Mudança em `senhas`:** o campo `guiche_local` (VARCHAR livre, seção 4 original)
passa a ser substituído por `local_id FK -> locais` (manter `guiche_local` como
coluna morta/legada por 1 deploy, igual ao tratamento dado a `servicos` no
adendo 10.1, até confirmar migração sem perda de dado histórico).

**Comportamento na tela de Atendimento:**
- Ao logar ou iniciar o turno, o operador **escolhe** o Local entre os cadastrados
  para a(s) unidade(s) que ele tem permissão de acessar — não é mais digitado
  livremente.
- A escolha do local fica na sessão do operador durante aquele turno/login,
  podendo ser trocada manualmente (ex.: um link/botão "Trocar local" na própria
  tela de Atendimento), sem precisar deslogar.
- CRUD de `locais` é administrativo (perfil `admin`/super admin), seguindo o
  mesmo padrão de isolamento multi-tenant e RBAC já estabelecido (404 ao tentar
  acessar local de outra empresa via ID manual, etc.).

**Relatórios (seção 7):** os agrupamentos "por setor (recepção/coleta)" passam
a poder usar também o agrupamento por `local_id` quando fizer sentido (ex.:
tempo médio de atendimento por guichê específico, não só por setor genérico).

---

## 10.4. ADENDO — Tipos de senha configuráveis (prefixo/cor/peso)

> Origem: documento de referência de especificação enterprise (estilo hospital
> de grande porte, ONA/JCI). A maior parte daquele documento é desproporcional
> para este projeto (ver nota de escopo ao final desta seção) — só o conceito de
> "tipo de senha" com regras próprias foi adotado, adaptado ao modelo já existente.

**Nova tabela `tipos_senha`:**
```
tipos_senha
  id PK
  empresa_id FK
  nome              -- ex: "Idoso", "Gestante", "PCD", "Oncologia", "Normal"
  prefixo           VARCHAR(2)  -- combinado com a sigla da fila na composição
                                  -- do código final (ver regra de composição abaixo)
  cor               VARCHAR(7)  -- hex, usado no totem/painel (ex: "#E53935")
  peso_prioridade   TINYINT DEFAULT 0  -- maior peso = preferência no rodízio
  tempo_maximo_min  INT NULL    -- referência para alertas/relatórios (opcional)
  ativo
```

**Composição do código da senha:** a regra original da seção 2 (sigla da fila +
sequencial) se mantém como base, e o prefixo do tipo de senha se soma a ela.
Exemplo: fila "Laboratório" (sigla `LAB`) + tipo "Idoso" (prefixo `I`) gera
`LAB-I001`, enquanto o tipo "Normal" (prefixo vazio ou `N`) gera `LAB001` ou
`LABN001`, dependendo da configuração de exibição (definir um campo
`exibir_prefixo_tipo_quando_normal` booleano na fila para esse ajuste fino,
default `false` — não exibir prefixo redundante quando for o tipo padrão).

**Vínculo com o rodízio de prioridade já existente (`prioridade_rodizio` em
`filas`, seção 5.4):** o `peso_prioridade` do tipo de senha escolhido no totem
é o que efetivamente entra no cálculo de "quantas chamadas seguidas" daquele
nível antes de voltar ao rodízio normal — não se cria um sistema de prioridade
paralelo, o tipo de senha é o que *alimenta* o rodízio que já temos.

**No totem:** ao tocar um botão-folha vinculado a uma fila, se a fila tiver mais
de 1 tipo de senha ativo configurado, exibir uma tela intermediária para o
paciente escolher o tipo (ex.: "Normal" ou "Prioritário/Idoso/Gestante/PCD")
antes de gerar o código — se só houver 1 tipo ativo (ou nenhum configurado),
gerar direto como hoje, sem essa tela extra, para não adicionar fricção
desnecessária em filas simples (ex.: Laboratório pode não precisar de tipos).

**No painel/TV:** usar a `cor` do tipo de senha para destacar visualmente a
linha daquela chamada (ex.: senhas prioritárias com fundo diferenciado).

**Cadastro administrativo:** CRUD de `tipos_senha` por empresa (não por
unidade — o catálogo de tipos é da empresa toda, reaproveitado entre unidades),
seguindo o mesmo padrão de RBAC e isolamento multi-tenant já validado.

---

### Nota de escopo — o que NÃO foi adotado do documento de referência enterprise

Para deixar explícito e evitar que sessões futuras do Claude Code tentem
reintroduzir isso "para completude": as seguintes partes do documento de
referência foram **deliberadamente descartadas** por serem desproporcionais
ao contexto real deste projeto (uma clínica, equipe pequena, Flask+MySQL
local) — não implementar nenhum destes itens a menos que o cliente solicite
explicitamente em uma decisão futura própria, com seu próprio adendo:

- Infraestrutura: Redis, RabbitMQ, Docker, Kubernetes, microsserviços,
  Prometheus/Grafana, CI/CD — manter Flask + MySQL + Gunicorn/eventlet +
  Apache2/Nginx conforme seção 10 original.
- Integrações externas: HL7, FHIR, TISS, ERP, HIS, LIS, RIS, Prontuário
  Eletrônico, Portal do Paciente, WhatsApp, SMS, Email, Webhooks.
- OAuth2/JWT/MFA — a autenticação por sessão (Flask-Login) já validada nas
  Fases 1-2 é suficiente para este contexto; não migrar para token-based
  sem necessidade real identificada.
- Múltiplos idiomas no totem, Libras, leitura por voz no totem (TTS já
  existe no painel/TV conforme seção 6; não duplicar no totem sem demanda).
- Dashboard BI separado, mapas de calor, gráficos avançados — os relatórios
  da seção 7 (tabelas + exportação CSV/Excel) atendem a necessidade atual;
  visualização gráfica pode ser um adendo futuro isolado, se necessário.
- Estados de senha além dos já definidos na seção 4 (`aguardando`, `chamada`,
  `em_atendimento`, `finalizada`, `cancelada`) — rechamada/transferência já
  são cobertas pelos botões Tocar/Realocar da seção 10.2 sem precisar de
  novos valores de enum; "ausente"/"expirada" não têm necessidade identificada
  agora.

---

## 10.5. ADENDO — Navegação multi-nível do totem + quantidade de acompanhantes

> **NOTA CRUZADA:** a parte "Quantidade de acompanhantes" desta seção foi
> **removida** a pedido do cliente - ver **10.34** (a etapa de UI que pedia o
> número saiu; a coluna `Senha.qtd_acompanhantes` foi mantida, só desativada).
> O resto desta seção (navegação multi-nível do totem) continua valendo.

> Baseado em mapeamento detalhado da navegação real do sistema de referência
> (ChameFácil/Prolins), confirmando e expandindo a hierarquia de botões já
> prevista na seção 5.5 original.

**Profundidade de navegação arbitrária:** a hierarquia de `totem_botoes`
(via `parent_id`, seção 4) já suporta múltiplos níveis por natureza da
modelagem (auto-relacionamento), mas fica confirmado aqui que isso é usado
**na prática** com 2 a 3 níveis de profundidade, não só 1 submenu simples.
Exemplo observado:
```
Tela raiz
 ├─ ULTRASSONOGRAFIA (botão-folha, gera senha direto)
 ├─ LABORATÓRIO (botão-folha, gera senha direto)
 ├─ CONSULTÓRIOS (menu, sem fila_id)
 │   ├─ Atendimento Idoso Acima de 80 Anos Lei 13466 (botão-folha → fila própria)
 │   ├─ Atendimento Normal (botão-folha → fila própria)
 │   ├─ Atendimento Preferencial Lei 10048 (botão-folha → fila própria)
 │   ├─ BRONCOSCOPIA (botão-folha → fila própria)
 │   ├─ ... (demais tipos/serviços do mesmo nível, todos folhas, caminhos
 │   │      independentes entre si — não há ordem obrigatória entre eles)
 │   └─ [outro grupo, ex. lista de médicos, também poderia estar neste
 │        mesmo nível ou em um filho de "CONSULTÓRIOS", dependendo de como
 │        o admin organizar; ambos os grupos são igualmente válidos como
 │        filhos diretos de um menu, sem relação de dependência entre eles]
 ├─ QUIMIO/AMBULATÓRIO-MEZANINO1 (menu, sem fila_id)
 │   ├─ ADMINISTRAÇÃO DE MEDICAMENTOS M1 (botão-folha → fila própria)
 │   └─ AUTORIZAÇÃO DE GUIAS (botão-folha → fila própria)
 └─ QUIMIO/AMBULATÓRIO-MEZANINO2 (menu, sem fila_id)
     ├─ ADMINISTRAÇÃO DE MEDICAMENTOS M2 (botão-folha → fila própria)
     ├─ ANEXO M2 (ORAIS E INJEÇÕES SC) (botão-folha → fila própria)
     ├─ AUTORIZAÇÃO DE GUIAS M2 (botão-folha → fila própria)
     ├─ EQUIPE MULTIDISCIPLINAR (botão-folha → fila própria)
     └─ TRANSFUSÃO (botão-folha → fila própria)
```
Importante: dentro de um mesmo menu (ex.: CONSULTÓRIOS), os botões-folha que
representam **tipos de atendimento** (Idoso, Normal, Preferencial, etc.) e os
que representam **médicos específicos** (caso cadastrados) são **caminhos
independentes, lado a lado, no mesmo nível** — não há uma navegação obrigatória
"primeiro escolha o tipo, depois o médico" nem o inverso. Cada botão-folha é
sua própria fila com sua própria sigla, conforme já estabelecido no adendo 10.1.
A interface de cadastro de botões (seção 5.5/Fase 3) já permite isso livremente,
sem necessidade de mudança de schema — este adendo é só uma confirmação de uso,
não uma alteração estrutural.

**Vínculo com `tipos_senha` (adendo 10.4):** os botões "Atendimento Idoso Acima
de 80 Anos Lei 13466 2017" e "Atendimento Preferencial Lei 10048 2000" não são
uma coleção própria — são **filas** cujo nome reflete o `tipo_senha` predominante
esperado ali, mas o catálogo de `tipos_senha` continua único por empresa
(reaproveitado entre todas as áreas: Consultórios, Quimio, etc.), conforme já
definido. Não criar uma segunda tabela de tipos específica de Consultórios.

**Novo: campo "Quantidade de acompanhantes" no momento de gerar a senha:**
- Modal exibido ao tocar um botão-folha, antes da geração efetiva do código:
  "Toque no número de pessoas que estão lhe acompanhando" — opções de 0 a 6
  (configurável: o limite máximo pode ser um parâmetro da fila, default 6).
- Natureza **somente informativa/estatística** — não gera senhas extras, não
  altera o código gerado, não afeta o rodízio de prioridade.
- Adicionar campo `qtd_acompanhantes` (TINYINT, default 0, nullable) em `senhas`.
- Uso pretendido: relatórios futuros de fluxo de pessoas/capacidade de sala de
  espera (não implementar o relatório agora, só capturar o dado desde já, para
  não perder histórico enquanto a funcionalidade de relatório não existe).
- Botão "Gerar Senha" no modal confirma; botão "Cancelar" volta sem gerar nada.

---

## 10.6. ADENDO — Impressão física da senha (impressora térmica Bematech, USB, Windows)

> Resolve o ponto que ficou aberto desde a seção 11 original ("Impressão física
> da senha: impressora térmica local no totem ou só exibição em tela?"). A
> resposta é: sim, impressão física, em ambiente Windows, com impressora
> Bematech (modelo MP-4200 TH ou similar) conectada via USB, já instalada e
> reconhecida pelo Windows em "Dispositivos e Impressoras".

**⚠️ SUPERSEDIDO pelo adendo 10.28 — impressão silenciosa via `win32print`/ESC-POS
substituída por impressão via diálogo nativo do navegador (`window.print()`),
com prévia visual do cupom antes de imprimir.** Motivo: o cliente pediu uma
prévia visual do comprovante antes da impressão efetiva, o que a abordagem
silenciosa (envio direto pra fila de impressão do Windows, sem preview) não
permite. O texto abaixo permanece como registro histórico da decisão
original — ver o adendo 10.28 para o mecanismo vigente hoje.

**Abordagem técnica escolhida:** em vez de comunicação USB raw (que exige
drivers libusb/WinUSB e é frágil no Windows), enviar os comandos ESC/POS
através da **fila de impressão nativa do Windows**, usando a biblioteca
`pywin32` (`win32print`). A impressora já está instalada e visível no sistema
operacional, então o totem só precisa montar a sequência de bytes ESC/POS e
enviá-la como um trabalho de impressão RAW para o nome da impressora
configurado (ex.: "Bematech MP-4200 TH"), sem precisar de driver adicional
nem de biblioteca de acesso a USB de baixo nível (`pyusb`).

**Biblioteca para montar os comandos ESC/POS:** usar `python-escpos`, que tem
suporte nativo a perfis Bematech (`escpos.impl.bematech` no PyESCPOS, ou
`profile="..."` no python-escpos), gerando os bytes corretos sem precisar
escrever os comandos hexadecimais manualmente. A camada de **envio** desses
bytes, porém, deve ser via `win32print` (fila do Windows), não via a conexão
USB direta que essas bibliotecas oferecem por padrão — combinar: usar a
biblioteca só para *gerar* a sequência de bytes, e `win32print` para *enviar*.

**Novo campo de configuração:**
```
empresas (ou unidades, ou tvs/totens — definir o nível mais adequado)
  nome_impressora_windows  VARCHAR(120) NULL
    -- nome exato da impressora como aparece em "Dispositivos e Impressoras"
    -- do Windows daquele totem específico, ex: "Bematech MP-4200 TH"
```
Como cada totem físico pode ter sua própria impressora local, o campo deve
ficar associado ao **totem** (ou à TV/totem físico, conforme a tabela de
cadastro de totens já mencionada no adendo 10.1), não à empresa inteira —
permitindo que totens diferentes, em PCs diferentes, apontem para impressoras
diferentes.

**Layout do cupom impresso:**
```
        [Nome da empresa/clínica]
              CM03
        24/06/2026  14:32
```
(mockup original tinha linhas tracejadas separadoras entre os blocos -
removidas depois a pedido do cliente, ver nota na 10.38; layout atual sem
separador, respiro visual só por espaçamento.)
- Nome da empresa: vem do cadastro de `empresas` já existente.
- Código da senha: fonte grande (usar comando ESC/POS de tamanho de fonte
  duplo/triplo, já suportado nativamente pela biblioteca escolhida).
- Data/hora: momento da impressão (= momento da geração da senha).
- Corte de papel automático ao final (`cut()`), se a impressora suportar
  (Bematech MP-4200 TH normalmente suporta guilhotina).

**Tratamento de falha:** se a impressão falhar (impressora desligada, sem
papel, fila travada), o totem **não deve travar nem impedir** a geração da
senha — a senha já foi gravada no banco e deve continuar seu fluxo normal
(aparecer na fila, poder ser chamada, etc.) mesmo sem o papel saindo. Registrar
a falha de impressão em log (não em `logs_atendimento`, que é sobre o fluxo de
atendimento — usar log de aplicação simples, ex.: arquivo de log do Flask),
e opcionalmente exibir um aviso discreto na tela do totem ("Anote seu código:
CM03" como reforço, sem bloquear o fluxo).

**Fora de escopo deste adendo:** múltiplas impressoras de rede, fila de
impressão remota, monitoramento de nível de papel em tempo real — implementar
apenas o caminho feliz (impressão local USB-via-Windows) mais a tolerância a
falha descrita acima.

---

## 10.7. ADENDO — Link direto de acesso público nas telas de Totens e TVs

> Falha de usabilidade identificada: as telas administrativas de Totens e TVs
> não exibem a URL pública de cada item, obrigando consulta direta ao banco
> de dados para descobrir o ID e montar a URL manualmente. Corrigir nas duas
> telas (Totens e TVs), não só em uma.

**Tela de Totens (`/admin/totens`):** adicionar coluna **"Link"** (ou ação
"Abrir totem") na tabela de listagem, com a URL pública completa e clicável
(abre em nova aba), no formato já estabelecido (`/totem/<id>` ou equivalente
ao que já está implementado). Incluir também um botão de **copiar link**
(ícone ou texto "Copiar"), já que totens físicos costumam precisar que esse
link seja configurado manualmente no navegador do equipamento em modo
quiosque — copiar evita erro de digitação.

**Tela de TVs (`/admin/tvs`):** mesma correção — coluna "Link"/ação "Abrir
painel" com a URL pública (`/painel/tv/<id>` ou equivalente), clicável, com
botão de copiar.

**Padrão a seguir para futuras telas:** qualquer entidade cadastrável que
gere uma URL pública de acesso (sem necessidade de login) deve exibir esse
link diretamente na listagem administrativa correspondente, como regra geral
de usabilidade deste sistema — evitar depender de consulta ao banco para
operação rotineira.

---

## 10.8. ADENDO — Rodízio de chamadas por Local (múltiplas filas)

> Baseado em telas administrativas do sistema de referência (ChameFácil/Prolins):
> lá, a tela de Atendimento é organizada por **Local**, não por Fila — um Local é
> "alocado" com uma lista ordenada de múltiplas filas, cada uma com uma
> "quantidade de chamadas seguidas", e o sistema revezar automaticamente entre
> elas, sem o operador escolher fila manualmente. Isso é um nível acima do
> `prioridade_rodizio` já existente (seção 5.4/adendo 10.2), que só revezar
> prioritário-vs-normal **dentro** de uma única fila. Este adendo formaliza a
> avaliação já feita: **seguimos com a opção completa** — rotação automática
> entre múltiplas filas por Local, com tela de Atendimento unificada
> **coexistindo** com a tela atual por `fila_id`, não a substituindo.

### 1. Modelo de dados — `LocalRodizioFila` (association object)

```
local_rodizio_filas
  local_id FK -> locais              -- PK composta com fila_id
  fila_id  FK -> filas
  ordem              TINYINT DEFAULT 0  -- posição da fila na lista (desempate/exibição)
  chamadas_seguidas  TINYINT DEFAULT 1  -- quantas chamadas consecutivas dessa fila
                                          -- antes de passar para a próxima da lista
```

**Por que precisa ser uma classe mapeada (model próprio), não uma tabela
associativa simples:** todas as relações N:N já existentes no projeto
(`usuario_perfil`, `usuario_unidade`, `tv_filas`, `fila_tipos_senha`,
`local_filas_destino` — esta última do bloco "Redirecionar" já implementado)
são tabelas puras, só com as duas FKs como chave composta, sem nenhum dado
extra por par. `local_rodizio_filas` precisa carregar `ordem` e
`chamadas_seguidas` por par Local×Fila — não cabe numa tabela associativa
simples, exige um `db.Model` de verdade (`LocalRodizioFila`), com
`db.relationship` pros dois lados (`local`, `fila`). É a primeira relação
N:N do projeto com esse formato — outras telas que reaproveitarem esse
padrão no futuro devem seguir o mesmo modelo (classe mapeada, não
`db.Table()`), sempre que precisarem de dados por par além das FKs.

**Mesma unidade obrigatória (Local e Fila do rodízio):** validado na camada
de aplicação (tela admin, seção 2 abaixo), nunca no schema — mesmo padrão já
estabelecido para `local_filas_destino`.

**Requisito explícito de migração** (ver seção 5): a tabela nasce vazia. Um
Local sem nenhuma linha aqui não entra no fluxo novo — ver seção 5.

### 2. Tela admin — cadastro de rodízio por Local

**Não é uma aba dentro do formulário de cadastro de Local** (`admin/locais/form.html`,
adendo 10.3) — vira uma **tela dedicada separada**, pelos mesmos motivos que
"Alocar fila" (seção 5.4) já é separada do cadastro de `Fila`: a interação
(lista ordenada + um número por item) é mais complexa que os campos simples
do formulário de Local (nome/unidade/tipo + os checkboxes de
`filas_destino_permitidas`), e misturar as duas deixaria o formulário
principal poluído.

**Diferente de "Alocar fila" (que vive em `atendimento_bp`, acessível a
perfis operacionais `recepcao`/`coleta`/`admin`):** esta tela fica em
`admin_bp`, só para `admin`/super admin — decisão deliberada, porque
configurar **quais filas um Local atende e em que ordem** é uma decisão
estrutural de operação da unidade (mais parecida com o cadastro do próprio
Local e com `filas_destino_permitidas`, ambos já admin-only), não um ajuste
fino do dia a dia como `prioridade_rodizio` costuma ser.

**Rotas propostas** (mesmo padrão de `autorizar_objeto`/`resolver_empresa_contexto`
já usado em `admin/locais.py`):
```
GET  /admin/locais/<int:local_id>/rodizio
POST /admin/locais/<int:local_id>/rodizio
```

**Template novo** (`admin/locais/rodizio.html`): lista as filas da MESMA
unidade do Local (igual ao filtro já usado nos checkboxes de
`filas_destino_permitidas`), cada uma com:
- checkbox "participa do rodízio deste Local";
- campo numérico `chamadas_seguidas` (default 1, mínimo 1);
- campo numérico `ordem` (posição na lista — sem drag-and-drop/JS, consistente
  com o restante do admin, que já é JS-light).

Ao salvar, substitui a lista inteira (mesmo padrão já usado em `tv.filas`/
`local.filas_destino_permitidas` — mais simples que calcular diff de
adição/remoção, e o volume por Local é pequeno).

**Aviso obrigatório na tela:** ao salvar uma configuração com 1+ fila, exibir
texto explícito — *"Ao salvar, este Local passará a usar a tela unificada de
Atendimento por Local (rotação automática entre as filas configuradas), em
vez da escolha manual de fila."* — mitiga o risco de confusão sobre qual modo
cada Local está usando (ver seção 7).

**Link de entrada:** nova ação "Configurar rodízio" na listagem
(`admin/locais/listar.html`), ao lado de "Editar"/ativar-desativar.

### 3. Nova rota de Atendimento — `/atendimento/<setor>/local/<local_id>`

**Coexiste** com `/atendimento/<setor>/filas/<fila_id>` (adendo 10.2), que
continua existindo sem nenhuma mudança de comportamento. Reaproveitamento:

- Mesmo template base (`atendimento/_layout.html`) e estrutura visual de
  `painel_fila.html` (Local / Senha chamada / Código / Nome / Redirecionar /
  botões de ação / tabelas "Senhas em espera" e "Senhas atendidas") — um novo
  `atendimento/painel_local.html` adaptado, não uma tela do zero.
- **O que muda:** "Senhas em espera" passa a agregar candidatas de **todas**
  as filas do rodízio daquele Local (nova função `senhas_aguardando_local(local)`,
  filtrando `Senha.fila_id.in_(ids_das_filas_do_rodizio)` em vez de uma só
  fila). O botão "Chamar" delega para `selecionar_proxima_fila_e_senha(local)`
  (seção 4) em vez de `selecionar_proxima_senha(fila)` diretamente — decide
  tanto a fila quanto a senha.
- **`senha_atual_no_local`/`senhas_atendidas_no_local` generalizadas:** hoje
  filtram por `fila_id` **e** `local_id` simultaneamente (`atendimento/helpers.py`)
  — precisam soltar o filtro de `fila_id` fixo (ou trocar por `fila_id.in_(...)`)
  quando chamadas a partir da tela por-Local, mantendo o comportamento atual
  (filtro fixo por uma fila) intacto para a tela por-fila existente. Ou seja:
  as funções ganham um modo mais genérico, não são reescritas — a tela por-fila
  continua passando 1 fila só, sem notar diferença.
- **Tocar/Finalizar/Aguardar:** operam sobre "a senha atualmente chamada neste
  Local" (independente de qual fila) — mesma lógica de registro
  (`registrar_toque`, `registrar_fim_atendimento`, `registrar_volta_espera`),
  que já recebem a `senha` já resolvida, não a fila — reaproveitamento total,
  sem mudança nelas.
- **Realocar:** sem mudança nenhuma — já opera sobre `senha.fila_id` e o bloco
  Redirecionar (`filas_destino`, restrição por Local já implementada),
  independente de qual tela (por-fila ou por-Local) disparou a ação.
- **Observações:** já é `senha_id`-scoped (`salvar_observacoes`), sem mudança.

**Rotas de ação:** `chamar_local`/`tocar_local`/`finalizar_local`/`aguardar_local`
(setor, local_id) — variantes da tela por-Local, espelhando as 4 já existentes
por-fila. `realocar` pode ser reaproveitada tal como está (não depende de
`fila_id` na URL de forma essencial, só usa isso pra montar o redirect de
volta — a variante `_local` troca só o redirect de destino).

### 4. `selecionar_proxima_fila_e_senha(local)` — lógica de decisão

Mesmo princípio de design já usado em `prioridade_rodizio`/`chamadas_recentes`
(seção 5.4/`atendimento/helpers.py`): **nenhum contador é persistido** — a
posição atual do rodízio é recalculada a partir do histórico real de
`Senha.chamada_em` a cada chamada.

```
def selecionar_proxima_fila_e_senha(local):
    candidatas = [
        item.fila for item in local.rodizio_ordenado  # ordenado por `ordem`
        if item.fila.ativo and item.fila.aberta_em()   # pula fila fechada/inativa
    ]
    if not candidatas:
        return None, None  # nenhuma fila do rodízio disponível agora

    # reconstrói de qual fila foram as últimas chamadas NESTE local (não
    # precisa contador novo - mesmo princípio de prioridade_rodizio)
    ultimas_chamadas = Senha.query.filter(
        Senha.local_id == local.id, Senha.chamada_em.isnot(None),
    ).order_by(Senha.chamada_em.desc()).limit(SOMA_CHAMADAS_SEGUIDAS_CONFIGURADAS).all()

    fila_da_vez = _proxima_fila_na_rotacao(candidatas, ultimas_chamadas)

    # delega para a função já existente, sem mudança - é aqui que os dois
    # rodízios se encontram (ver seção "Coexistência" abaixo)
    senha = selecionar_proxima_senha(fila_da_vez)

    # fila da vez está sem ninguém esperando - percorre as demais, na ordem,
    # até achar uma com gente esperando (não trava o rodízio numa fila vazia)
    if senha is None:
        for fila in candidatas:
            if fila.id == fila_da_vez.id:
                continue
            senha = selecionar_proxima_senha(fila)
            if senha is not None:
                fila_da_vez = fila
                break

    return fila_da_vez, senha
```

`_proxima_fila_na_rotacao`: conta, a partir da chamada mais recente pra trás,
quantas foram seguidas da MESMA fila; se esse número ainda for menor que o
`chamadas_seguidas` configurado pra ela, repete a mesma fila; senão avança
pra próxima da lista (por `ordem`), ciclicamente ao chegar no fim.

**`SOMA_CHAMADAS_SEGUIDAS_CONFIGURADAS` é dinâmica, não uma constante fixa**
(observação registrada na revisão do adendo, antes da Fase 10.8-C) — é a soma
de `chamadas_seguidas` de todas as filas candidatas **daquele Local
especificamente** (`sum(item.chamadas_seguidas for item in candidatas)`),
recalculada a cada chamada junto com a lista de candidatas (que já pula filas
fechadas/inativas). Locais diferentes, com rodízios configurados de forma
diferente, têm essa soma diferente — nunca um valor fixo global. Isso precisa
ficar explícito no código da Fase 10.8-C (não só no pseudocódigo) e coberto
por teste que confirma o comportamento com pelo menos dois Locais com somas
distintas.

**Coexistência com `prioridade_rodizio` — não competem, são duas camadas:**
- **Camada externa (nova, deste adendo):** qual fila é a vez, dentre as N
  filas do rodízio do Local.
- **Camada interna (já existe, sem nenhuma mudança):** dentro da fila
  escolhida, `prioridade_rodizio` decide prioritário-vs-normal, exatamente
  como hoje (`selecionar_proxima_senha(fila)` reaproveitada sem alteração).

Nenhuma fila muda de comportamento só por essa feature existir —
`prioridade_rodizio` continua sendo lido e aplicado igual, tanto por filas
que participam de um rodízio de Local quanto pelas que continuam no fluxo
por-fila tradicional.

### 5. Migração — requisito explícito de compatibilidade retroativa

**Requisito, não implícito:** um Local **sem nenhuma linha** em
`local_rodizio_filas` continua funcionando **exatamente como hoje** — fluxo
manual por `fila_id`, via `listar_filas()` →
`/atendimento/<setor>/filas/<fila_id>`. `escolher_local()` não muda de
comportamento para esses Locais; nenhum redirecionamento automático ocorre.
A tabela nasce vazia (migration só cria o schema, sem popular nada) — **zero
impacto em qualquer Local/fila já cadastrado** até que um admin configure
explicitamente um rodízio via a tela da seção 2.

Só Locais com 1+ linha configurada entram no fluxo novo — e mesmo esses
continuam com a URL antiga (`/atendimento/<setor>/filas/<fila_id>`) acessível
por quem quiser (ex.: um operador pode preferir a tela de uma fila específica
mesmo com o Local tendo rodízio configurado) — não há bloqueio de acesso à
tela por-fila, só uma sugestão de fluxo automático quando o Local está
configurado.

### 6. Fases de implementação sugeridas

Mesmo espírito da seção 9 original — uma fase por vez, aprovando antes de
avançar (mesmo processo já usado no dashboard fora do BLUEPRINT):

| Fase | Entrega |
|---|---|
| **10.8-A** | Model `LocalRodizioFila` + migration (tabela nova, vazia — zero impacto) |
| **10.8-B** | Tela admin de configuração (`/admin/locais/<id>/rodizio`) — CRUD da lista ordenada, validação de mesma unidade, aviso de mudança de fluxo |
| **10.8-C** | `selecionar_proxima_fila_e_senha(local)` + generalização de `senhas_aguardando_local`/`senha_atual_no_local`/`senhas_atendidas_no_local` — só a lógica, testável isoladamente, sem tela nova ainda |
| **10.8-D** | Nova rota `/atendimento/<setor>/local/<local_id>` (GET + `/dados`) e template `painel_local.html` — só visualização, sem botões de ação ainda |
| **10.8-E** | Botões de ação na tela nova (chamar/tocar/realocar/finalizar/aguardar por Local) + integração WebSocket (múltiplas salas `fila_<id>` agregadas no cliente) |
| **10.8-F** | `escolher_local()` passa a oferecer/redirecionar para a tela nova quando o Local tiver rodízio configurado |

### 7. Riscos identificados e mitigação

| Risco | Mitigação |
|---|---|
| Confusão sobre qual Local usa qual modo (por-fila vs. rodízio) | A mera presença de linhas em `local_rodizio_filas` já decide isso automaticamente (sem campo de flag extra) — mitigado com o aviso explícito na tela de configuração (seção 2) |
| Duas telas de atendimento mantidas em paralelo = manutenção dobrada a longo prazo | Reaproveitamento máximo de `atendimento/helpers.py` — só a *seleção* de fila/senha muda; `registrar_*` já são fila/senha-agnósticas |
| Rodízio "preso" numa fila configurada mas vazia (sem ninguém esperando) | Pseudocódigo da seção 4 já prevê fallback: percorre as demais filas da lista até achar uma com gente esperando, sem travar |
| Todas as filas do rodízio fechadas ao mesmo tempo (`horario_funcionamento`, fim de expediente) | Tela deve mostrar mensagem clara ("Nenhuma fila disponível neste horário"), nunca erro/quebra |
| Rodízio do Local configurado com filas que não têm relação com `filas_destino_permitidas` (bloco Redirecionar) daquele mesmo Local | Não travar — só exibir aviso na tela de configuração se as listas não se sobrepõem, já que são conceitos relacionados mas independentes (rotação de chamada ≠ destinos de redirecionamento) |
| WebSocket com N salas simultâneas na mesma conexão | Nenhuma infraestrutura nova necessária — `entrar_fila` já é chamável múltiplas vezes por conexão (mesmo padrão de `entrar_tv` iterando `tv.filas`); complexidade fica só no JS do cliente, que precisa agregar eventos de N filas na mesma tela |

---

## 10.9. ADENDO — Super admin global criável pelo próprio painel

> Dor operacional relatada: o único jeito de criar um segundo super admin
> (`empresa_id IS NULL`) era `flask shell`/acesso direto ao banco. O primeiro
> super admin (`superadmin`, seeds/seed_inicial.py) não tinha como delegar
> esse nível de acesso a outra pessoa sem acesso ao servidor.

**Nenhuma mudança de schema** — `Usuario.is_super_admin` já era só uma
`@property` (`empresa_id is None`), sem coluna própria. Todo o RBAC do sistema
(`g.is_super_admin`, `tem_acesso_unidade`, `autorizar_objeto`) já dava bypass
total pra super admin, com ou sem perfil/unidade atribuída — só faltava um
caminho no painel pra gravar essa linha (`empresa_id=None`).

**Onde fica na UI:** `/admin/usuarios` sem `empresa_id` selecionado já mostra,
pro super admin, a tela de escolher empresa (`admin/selecionar_empresa.html`)
— essa mesma tela ganhou uma seção "Super admins (sem empresa)" acima da
lista de empresas, com badge própria (`.badge.super-admin`, roxo) distinguindo
de admin de empresa, e um botão "+ Novo super admin".

**Rotas novas** (`app/admin/usuarios.py`):
- `POST /admin/usuarios/globais/novo` (`novo_usuario_global`) — só nome/login/
  senha, sem perfis/unidades (mesmo formato do usuário `superadmin` semeado);
  `abort(403)` se `not g.is_super_admin`, verificado no servidor, não só
  escondido na UI.
- `editar_usuario`/`alternar_usuario` (já existentes) passaram a detectar
  `usuario.empresa_id is None` e desviar pro formulário simples
  (`admin/usuarios/form_global.html`) e pro redirect correto (sem tentar
  montar `empresa_id=None` na querystring).

**RBAC (mantido):** só quem já é super admin pode criar outro — admin de
empresa nunca vê a opção nem consegue disparar a rota diretamente (403);
tentar editar/ativar um super admin global por ID direto também dá 404 pra
admin de empresa (`autorizar_objeto` já cobria esse caso, sem mudança).

---

## 10.10. ADENDO — Busca com autocomplete no bloco Redirecionar (seção 10.2)

> Dor de usabilidade real: com 30–50 médicos/filas cadastrados, a lista de
> radio buttons do bloco "Redirecionar" (seção 10.2) ficava inutilizável -
> rolagem enorme, sem busca.

**Substituição da lista estática por um campo de busca** (`atendimento/
_redirecionar_busca.html`, partial reaproveitado por `painel_fila.html` e
`painel_local.html` — primeiro `{% include %}` do projeto, justificado pelo
tamanho do componente): input de texto + dropdown filtrado **100% em
JavaScript local**, sem round-trip ao servidor por tecla. A lista completa de
filas candidatas (já calculada por `filas_destino()`, sem nenhuma mudança de
isolamento multi-tenant) vem embutida como JSON no HTML
(`#dados-filas-destino`), filtrada a partir de 2 letras digitadas.

**Indicador de horário de funcionamento no dropdown:** cada item usa
`Fila.aberta_em()` (já existente, adendo 10.1/10.5) pra se marcar como
"● Atendendo agora" (filas dentro do horário) ou "· Fora do horário" (fora) —
sem nenhuma lógica de horário nova, só reaproveitando o método já testado.
Agrupamento no dropdown: filas "atendendo agora" primeiro, depois as demais,
com divisor visual sutil só quando os dois grupos coexistem no resultado
filtrado.

**Contrato de formulário inalterado:** o widget produz o mesmo
`<input type="hidden" name="fila_destino_id">` que os radio buttons produziam
— as rotas `realocar`/`realocar_local` (`app/atendimento/chamada.py`) não
mudaram uma linha.

**Acessibilidade/touch:** alvos de toque de pelo menos 44px (campo de busca e
itens do dropdown), consistente com uso em totem/tablet.

> **CORRIGIDO** (mesma sessão de trabalho, seção 10.20 já em uso real): o
> filtro só abria a caixa de seleção a partir de 2 caracteres digitados —
> clicar/focar no campo vazio não dava nenhum retorno visual, parecendo
> quebrado. Removida a exigência de mínimo de caracteres: focar OU clicar no
> campo (mesmo já focado, mesmo vazio) agora abre a caixa de seleção
> imediatamente com TODAS as filas candidatas (mesmo agrupamento "atendendo
> agora" primeiro); digitar continua filtrando a partir daí, normalmente. O
> campo passa a se comportar como uma caixa de seleção de verdade, que só
> vai se estreitando conforme o operador digita — em vez de um campo de
> busca "cego" até a segunda letra.

> **EXTENSÃO** (mesma sessão de trabalho): o mesmo padrão de busca com
> autocomplete foi aplicado aos dois `<select>` simples da tela "Iniciar
> turno" (`atendimento/iniciar_turno.html`, seção 10.19) — "Fila" (o
> dropdown de adicionar fila ao rodízio do turno) e "Local" (o posto do
> turno) — 3ª ocorrência do mesmo padrão nesta sessão.
>
> **Investigação prévia (confirmada, não presumida):** o dropdown de Fila do
> `<select>` antigo já listava TODAS as filas, inclusive as já adicionadas à
> tabela "Filas do turno" (a mesma fila pode ser adicionada de novo só para
> ajustar o peso no rodízio — `_mesclar_filas_config`, `turno.py`, soma as
> entradas repetidas ao salvar) — comportamento preservado no widget novo,
> sem nenhum filtro adicional. O `<select id="select-local">` nunca teve
> nenhuma indicação visual de "Local ocupado por outro operador"
> (`locais_acessiveis()`/`_contexto_formulario` não carregam esse dado) —
> nada a replicar nesse sentido; a checagem de ocupação continua acontecendo
> só no POST (`turno.py`), como sempre foi.
>
> **Generalização decidida (registro do raciocínio):** o JS do widget 10.10
> (`_redirecionar_busca.html`) foi generalizado numa fábrica reaproveitável
> — `criarBuscaAutocomplete(cfg)`, parametrizada por ids de elementos, lista
> de itens, funções de busca/exibição, marcador opcional de "aberta" e valor
> inicial opcional — mas **dentro de `iniciar_turno.html`, não em
> `_redirecionar_busca.html`**: a 1ª instância já é testada/comprovada em
> produção com um contrato de formulário próprio (o hidden field É o campo
> enviado direto no submit de Realocar); refatorá-la juntaria risco a um
> widget que já funciona, sem necessidade real. As DUAS instâncias novas,
> por outro lado, vivem no mesmo template e são praticamente idênticas — a
> duplicação ENTRE ELAS é que foi eliminada.
>
> **Diferenças de contrato entre as 3 instâncias, preservadas:**
> - Fila: seleção não submete nada sozinha — só preenche um candidato
>   (`#fila-busca-id`, sem `name`, não viaja no POST); o botão "Adicionar
>   fila" existente lê esse valor, empurra pra `filasSelecionadas` (mesma
>   lógica de sempre) e reseta o widget (`buscaFila.limpar()`). Marcador
>   "Atendendo agora"/"Fora do horário" reaproveitado (`Fila.aberta_em()`,
>   novo campo `aberta` em `filas_json`, `_contexto_formulario`).
> - Local: o hidden field É o campo real do form (`name="local_id"`, igual
>   ao `<select>` antigo) — sem marcador de horário (não se aplica). Ganhou
>   suporte a valor inicial (`valorInicial`) para replicar a pré-seleção que
>   o `<select required>` fazia via `selected` ao reconfigurar um turno já
>   ativo — capacidade que o widget 10.10 original não precisava (nunca
>   inicia pré-selecionado). Como um hidden `required` não é validado
>   nativamente pelo navegador, a checagem "Local não pode ficar vazio" foi
>   replicada em JS (mesmo padrão do erro de "nenhuma fila selecionada" já
>   existente), com um novo parágrafo de erro (`#erro-local`) — o servidor
>   já validava isso de qualquer forma (`turno.py`), então não é uma
>   validação nova, só devolve o feedback imediato que o `required` dava.
>
> **Regressão real encontrada pela suíte, confirmada com o cliente antes de
> decidir (não corrigida por conta própria):** `test_regressao_contexto_
> multi_unidade.py` (item 7 do seu próprio docstring) já comprovava que os
> `<select>` de "Iniciar Turno" tinham `<optgroup>` **por unidade** —
> recurso deliberado de UX multi-unidade, próprio adendo, aplicado também a
> outros 3 selects do admin (Totem/TV no cadastro de Fila, Fila no cadastro
> de Botão do totem — esses 3 continuam `<select>` simples, INTOCADOS, ainda
> com `<optgroup>`). O widget de busca novo não agrupa por unidade — Fila
> agrupa por "atendendo agora"/"fora do horário" (padrão da 10.10), Local não
> agrupa nada — só o prefixo `[SIGLA] Nome` continua identificando a unidade
> de cada item. Perguntado ao cliente antes de prosseguir: confirmado manter
> só o prefixo (busca por nome/sigla já reduz a necessidade de navegar uma
> lista longa) — `test_regressao_contexto_multi_unidade.py` atualizado
> (checa `unidade_sigla`/`identificador_admin` no JSON embutido em vez de
> `<optgroup>` no HTML), sem perda de cobertura.
>
> **Testes:** `tests/test_regressao_busca_turno.py` (15 casos) — mesmo
> padrão de `test_regressao_redirecionar_busca.py`, adaptado às duas novas
> instâncias: ausência dos `<select>` antigos, campos/ids novos, JSON
> embutido com marcador `aberta` só na Fila, alvos de toque, ausência de
> mínimo de caracteres, fila já adicionada continua no dropdown, e a
> pré-seleção do Local via `valorInicial` ao reconfigurar um turno.
> `tests/test_regressao_turno_ativo.py`, `tests/test_regressao_single_
> session.py` e `tests/test_regressao_contexto_multi_unidade.py`
> (pré-existentes) tinham asserções amarradas ao markup antigo
> (`id="select-local"`, `selected`/`<optgroup>` em `<option>`) — atualizadas
> para os novos ids/contrato, sem mudança de cobertura.

---

## 10.11. ADENDO — Horário de relógio junto da duração (tela de Atendimento)

> Pedido direto: nas tabelas "Senhas em espera"/"Senhas atendidas" só existia
> tempo decorrido (`T. Espera`/`Tempo de espera`), sem o horário de relógio
> (ex.: "14:32") em que a senha foi gerada/chamada.

Adicionado um `<span class="hora-relogio" data-hora-de="...">` ao lado de
cada duração, em `painel_fila.html` e `painel_local.html` (renderização
inicial via Jinja e reconstrução via WebSocket). **A formatação do horário
acontece sempre no navegador** (`toLocaleTimeString`), nunca no servidor —
`criada_em`/`chamada_em` são gravados em UTC "naive" (`datetime.utcnow()`),
então formatar no Jinja mostraria a hora errada em servidores fora do fuso da
clínica; o mesmo truque (`+ "Z"` força interpretação UTC, o navegador
converte pro fuso local dele) já usado no cálculo do `T. Espera` ao vivo foi
reaproveitado.

`_serializar_atendida()` (`app/atendimento/chamada.py`) passou a incluir
`chamada_em` no JSON de `/dados` (antes só existia em `espera`), pro
refresh via WebSocket também conseguir montar o horário sem reload.

---

## 10.12. NOTA OPERACIONAL — Seed de Perfis é obrigatório em instalação nova

> Diagnóstico de um "bug" reportado (`/admin/perfis` vazio, checkboxes de
> Perfis sumidos na edição de usuário) que na verdade era dado faltando, não
> defeito de código — registrado aqui pra não se repetir em deploys futuros.

A tabela `perfis` (seção 3/4) é um **catálogo fixo do sistema** — não existe
(nem deve existir) CRUD de Perfil pela web (`app/admin/perfis.py` é só
leitura). Os 5 perfis padrão só são gravados por `seeds/seed_inicial.py`, que
também garante o usuário `superadmin` inicial. Em uma instalação/banco novo,
rodar migrations (`flask db upgrade`) cria o **schema**, mas não popula os
Perfis — é preciso rodar `python seeds/seed_inicial.py` explicitamente depois
(idempotente: seguro de rodar de novo, só insere o que faltar, nunca duplica
nem mexe em usuários/empresas já existentes).

---

## 10.12.2. ADENDO — Dashboard operacional pós-login (KPIs, gráfico, filas, últimos atendimentos)

> Pedido do cliente (Parte 2): tela `/dashboard` com 4 KPIs, gráfico de barras
> por hora (Chart.js), lista de filas com status colorido e tabela de últimos
> atendimentos; login passa a cair aqui em vez da tela genérica; KPIs
> atualizam via polling de 30s; filtros por perfil; nome do paciente exibido
> só pelo primeiro nome. Só CSS/HTML + a nova rota — nenhuma lógica de
> atendimento, WebSocket, banco de dados ou regra de negócio já existente foi
> alterada.

**1. Nova rota `GET /dashboard`** (`app/core/routes.py::dashboard`, blueprint
`core_bp`, path na raiz — distinta de `/relatorios/dashboard`, que **continua
existindo sem nenhuma mudança**, só deixou de ser o destino dos links "📊
Dashboard" do menu). Resolve a empresa com o mesmo `resolver_empresa_contexto()`
já usado em admin/relatórios (super admin escolhe via barra de contexto,
persistida em sessão — seção 10.22; qualquer outro perfil vê sempre a própria
empresa) e renderiza `core/dashboard.html`, uma página autocontida (não
estende `admin/_layout.html` nem `atendimento/_layout.html` — evita mostrar,
pra um atendente/executor, uma navbar cheia de links administrativos que
dariam 403 se clicados).

**2. Os 4 KPIs** (`app/core/dashboard_queries.py::calcular_kpis`):
- **Senhas aguardando agora** (`Senha.status == 'aguardando'`).
- **Chamadas hoje** (`LogAtendimento.evento IN ('chamada_senha',
  'chamada_nome')`, dia UTC atual — mesma janela de "hoje" já usada em todo o
  projeto pro reset diário do totem/histórico da TV; mesmo par de eventos
  `EVENTOS_INICIO_ATENDIMENTO` já usado em `relatorios/queries.py`, então
  não duplica uma visita "Chamar → Realocar com nome" como 2 chamadas —
  mesma correção da seção 7 já vale aqui de graça).
- **Tempo médio de espera hoje** (`gerada` → `chamada_senha`, só das senhas
  criadas hoje, agregado num único número — `None`, nunca `0`, quando ainda
  não há nenhuma chamada hoje).
- **Filas ativas** (`Fila.ativo == True`, escopadas por unidade visível).

**3. Filtro por perfil** (`unidades_visiveis_dashboard`, escopo NOVO,
específico desta tela — não reaproveita `_unidades_ids_visiveis` de
`relatorios/helpers.py`, que restringiria 'gerencial' às unidades
explicitamente atribuídas em `Usuario.unidades`):
- **Super admin**: escolhe a empresa (barra de contexto); dentro dela, vê
  todas as unidades por padrão, com a opção de estreitar pra uma só (mesmo
  select de Unidade da seção 10.22 — narrowing seguro, o teto de visibilidade
  dele já é irrestrito).
- **Admin/Gerencial**: veem a EMPRESA INTEIRA por padrão (decisão de produto
  específica do dashboard — diferente do resto de `relatorios/`, onde
  'gerencial' já podia ser restrito a unidades específicas), com a mesma
  opção de estreitar por unidade.
- **Atendente/Executor/Atendente_executor**: veem só as próprias unidades
  (`g.unidades_permitidas`, seção 10.19) — **sem** nenhum seletor de unidade
  na tela, e sem conseguir estreitar/ampliar via `?unidade_id=` na
  querystring (a função nem chama `resolver_unidade_contexto` pra esse
  grupo) — fecha por completo a possibilidade de ver dado de uma unidade que
  não é sua manipulando a URL.
- Perfis puramente operacionais continuam caindo em "Iniciar Turno" no login
  (seção 10.19/10.23, sem mudança) — mas nada os impede de visitar
  `/dashboard` manualmente depois, com o escopo acima.

**4. Login redireciona pra `/dashboard`** (`app/auth/routes.py::login`) — o
fallback que antes era `core.index` (tela genérica, com uma lista solta de
links) agora é `core.dashboard`, tanto no POST de login bem-sucedido quanto
no GET de quem já está autenticado e reabre `/login`. Perfis puramente
operacionais continuam indo pra `iniciar_turno` primeiro (checado antes,
sem mudança). `core.index` continua existindo e acessível (link "🏠 Painel
de Senhas" no menu, em todo layout), só deixou de ser o destino automático
do login.

**5. Polling dos KPIs a cada 30s** (`core/dashboard.html`, JS) — só os 4
cartões do topo são atualizados via `fetch()` periódico em
`GET /api/dashboard/kpis` (JSON, mesmo escopo por perfil do item 3); o
gráfico, a lista de filas e a tabela de últimos atendimentos continuam do
render inicial (recarregar a página inteira a cada 30s seria mais pesado que
o necessário e não foi pedido).

**6. Gráfico de barras por hora** (Chart.js, vendorizado em
`app/static/vendor/chart.umd.min.js` — mesmo padrão de dependência local já
usado pro Socket.IO, sem CDN externo) — o servidor manda só os timestamps
(ISO, UTC) das chamadas de hoje; o agrupamento por HORA em si é feito em
JavaScript, no fuso horário LOCAL de quem está vendo a tela (mesma convenção
já estabelecida no projeto: nunca formatar/agrupar hora-de-parede no
servidor, sempre no navegador).

**7. Nome do paciente — só o primeiro nome** (`primeiro_nome()`,
`app/core/dashboard_queries.py`) — aplicado na tabela de "Últimos
atendimentos" por privacidade/compacidade na tela; `nome_paciente` continua
gravado por inteiro no banco (seção 10.2, sem mudança), o truncamento é só
de exibição nesta tabela específica.

**8. Filas com status colorido** (`filas_com_status`): Ativa (verde, dentro
do horário agora — `Fila.aberta_em()`, sem lógica de horário nova), Fora do
horário (laranja — ativa mas fechada agora) ou Inativa (cinza) — reaproveita
as cores de badge já padronizadas no rebrand GESTFILA (seção 10.24).

**9. Templates alterados/criados** (só CSS/HTML + os dois endpoints novos -
nenhuma rota de atendimento, WebSocket, query ou regra de negócio já
existente foi tocada): `app/static/vendor/chart.umd.min.js` (novo, Chart.js
v4.5.1), `app/core/dashboard_queries.py` (novo), `app/core/routes.py`
(rotas `dashboard`/`api_dashboard_kpis` adicionadas),
`app/templates/core/dashboard.html` (novo), `app/auth/routes.py` (destino
do login), `app/templates/admin/_layout.html`,
`app/templates/atendimento/_layout.html`, `app/templates/relatorios/_layout.html`,
`app/templates/core/index.html` (link "Dashboard" repontado pra `core.dashboard`).

**Confirmação:** suíte completa (41 arquivos de teste) revalidada sem
nenhuma quebra, incluindo os testes já existentes de login, totem, painel de
TV e tela de Atendimento — nenhum desses fluxos foi afetado. Teste novo
dedicado: `tests/test_regressao_dashboard_operacional.py` (20 casos) - cobre
os 4 KPIs, filtro por perfil (admin/gerencial vendo a empresa inteira,
atendente restrito e sem conseguir escapar via querystring), isolamento
multi-tenant, o polling via `/api/dashboard/kpis`, o truncamento do nome do
paciente e os 3 status coloridos de fila.

---

## 10.13. ADENDO — Correções no painel/TV (duplicação, histórico, voz)

> Três problemas relatados juntos no painel de chamada (TV, seção 6): senha
> duplicando na lista, som não confiável, voz robótica.

**1. Duplicação ao usar "Tocar":** o botão "Tocar" (repete o áudio da senha
JÁ chamada, seção 10.2) reemitia o mesmo evento WebSocket `chamada` que
"Chamar" usa — a TV tratava toda `chamada` recebida como avanço novo,
empilhando uma linha idêntica a cada "Tocar" e inflando o contador.
`registrar_toque()` (`app/atendimento/helpers.py`) passou a marcar o payload
com `repetir: true`; `painel/tv.html` usa essa marca pra só re-destacar a
linha do topo (e repetir o áudio) em vez de duplicar.

**2. Histórico da TV não sobrevivia a reload/reconexão:** a lista de
chamadas só existia na memória do navegador (array JS) — um reload (ou a
recarga diária automática já prevista na seção 8) ou uma queda de rede
zerava tudo, mesmo com chamadas reais tendo acontecido. Nova função
`historico_chamadas_tv(tv, limite=8)` (`app/atendimento/helpers.py`)
reconstrói o histórico "de hoje" a partir de `Senha.chamada_em` (mesma
janela UTC usada no reset diário do sequencial, seção 8/10.1) e é enviada no
payload do evento `entrou` (`app/sockets/painel.py::entrar_tv`) toda vez que
a TV conecta ou reconecta — resolve reload **e** quedas de rede temporárias,
sem precisar de nenhuma lista de sessões em memória. Limitação aceita (mesmo
espírito da seção 7 sobre `logs_atendimento` não guardar snapshot por
evento): reflete o estado ATUAL de cada `Senha`, não um retrato de cada
chamada individual.

**3. Voz robótica:** a escolha de voz (`speechSynthesis.getVoices()`) pegava
a primeira voz `pt` disponível, tipicamente uma voz SAPI local clássica do
Windows. `vozPtPreferida()` (`painel/tv.html`) passou a pontuar as vozes
disponíveis, priorizando vozes "Online"/"Natural"/"Neural" (Edge/Windows mais
recente) e vozes "Google" (Chrome) sobre as locais — dentro do mesmo nível de
qualidade, prefere `pt-BR` sobre outras variantes. Nenhuma mudança se o
navegador só tiver vozes locais (mesmo comportamento de antes nesse caso).

**Nota — workaround revertido:** um keep-alive especulativo de
`speechSynthesis.pause()/resume()` (tentativa de mitigar um bug conhecido do
Chrome em sessões longas) foi adicionado e depois **removido** na mesma
sessão de trabalho, por ser não-verificável neste ambiente (sem runtime de
navegador nos testes) e ter relatos de também causar cortes de fala em vez de
só preveni-los — registrado aqui pra não ser reintroduzido sem validação
manual real em produção primeiro.

---

## 10.14. ADENDO — Exclusão segura (Excluir) nas telas administrativas

> Pedido: habilitar "Excluir" (exclusão de verdade, distinta do toggle
> Ativar/Desativar já existente) em Empresas, Filas, Totens, TVs, Locais,
> Tipos de senha e Botões do totem.

**Decisão de design confirmada antes de implementar:** exclusão **segura**,
não incondicional. Nenhuma FK do projeto tem `ondelete=CASCADE` — um DELETE
bruto num item com histórico falharia com erro de integridade referencial
(ou, pior, exigiria apagar `Senha`/`LogAtendimento` em cascata, o que
contraria diretamente a seção 8: *"nunca apagar senhas/logs_atendimento -
usar status, nunca DELETE"*). Cada rota `excluir_*` checa antes de excluir e,
se houver qualquer vínculo/histórico, **bloqueia com mensagem clara** e
sugere Desativar em vez de excluir:

| Entidade | Bloqueia a exclusão se... |
|---|---|
| **Fila** | já tem senha gerada (histórico), ou botão do totem vinculado |
| **Local** | já tem senha atendida ali (histórico) |
| **Tipo de senha** | já foi usado numa senha |
| **Totem** | alguma fila usa ele especificamente (`Fila.totem_id`) |
| **TV** | alguma fila usa ela como atalho de cadastro (`Fila.tv_id`) |
| **Botão do totem** | tem botão filho (hierarquia, `parent_id`) |
| **Empresa** | tem qualquer unidade/usuário/fila/local/totem/TV/tipo de senha/botão — só exclui empresa **vazia** (criada por engano, antes de qualquer cadastro) |

Quando **não** há bloqueio, a exclusão limpa sozinha os vínculos que são só
configuração (não histórico) antes de apagar a linha — ex.: excluir uma fila
remove os vínculos M2M com TVs/tipos de senha (`tv_filas`, `fila_tipos_senha`)
e as linhas de `local_filas_destino`/`local_rodizio_filas` que a referenciam,
mas nunca mexe na TV/tipo/Local em si; excluir um Local limpa o "local atual"
(`Usuario.local_atual_id`) de quem estava com ele selecionado.

**UI:** botão "Excluir" (`.link-perigo`, vermelho/negrito) em cada listagem,
com `confirm()` no navegador antes de enviar (ação irreversível).

---

## 10.15. ADENDO — Ocupação de locais (um usuário por Local) + logout remoto

> Pedido: impedir que dois usuários distintos ocupem o mesmo Local ao mesmo
> tempo, e dar ao admin uma tela pra ver quem está logado em qual Local e
> desconectar com um botão.

**1. Bloqueio de ocupação simultânea:** `escolher_local()`
(`app/atendimento/local.py`) passou a checar, antes de gravar
`Usuario.local_atual_id`, se **outro** usuário já ocupa aquele Local
(`Usuario.local_atual_id == local.id AND Usuario.id != current_user.id`). Se
sim, bloqueia com mensagem nomeando quem já está usando o local. O próprio
ocupante pode continuar reconfirmando/trocando de volta pro mesmo local sem
se autobloquear.

**2. Tela admin de ocupação** (`GET /admin/locais/ocupacao`, link "Ver
ocupação atual" na listagem de Locais): lista todos os usuários com um Local
escolhido agora, agregando **todas as unidades** da empresa de uma vez — nova
função `usuarios_ativos_da_empresa(empresa_id)` (`app/atendimento/
helpers.py`), irmã de `usuarios_ativos_por_local(unidade_id)` que já existia
"pronta pra uso futuro" desde o adendo 10.3.

**3. Botão "Desconectar" — libera o Local E derruba a sessão de login:**
```
usuarios
  ...
  sessao_versao  INTEGER NOT NULL DEFAULT 0   -- migration a1c4e8f2b7d3
```
A autenticação deste projeto é por cookie de sessão assinado (Flask-Login
padrão), sem lista de sessões no servidor — não existe, por padrão, uma forma
de "derrubar a sessão de outra pessoa à distância". O mecanismo adicionado:
o número atual de `sessao_versao` é gravado no cookie no momento do login
(`auth/routes.py::login`); a cada request, `load_user()` (`app/__init__.py`)
compara o número do cookie com o do banco — se não bater (foi incrementado
depois que o cookie foi emitido), a requisição é tratada como não autenticada
(`current_user` vira anônimo, `@login_required` redireciona pro login), sem
precisar de nenhuma lista de sessões revogadas em memória/banco. "Desconectar"
(`admin/locais.py::desconectar_usuario_local`) incrementa `sessao_versao` E
limpa `local_atual_id`/`local_desde` do usuário-alvo. Guarda: admin não pode
desconectar a si mesmo (mesmo padrão já usado em "não desativar a si mesmo").

---

## 10.16. DECISÃO DE UX — Botão "Chamar" oculto para perfis operacionais

> Pedido direto, só de UI: o botão "Chamar" principal (seção 10.2) deixou de
> aparecer pra quem tem perfil `recepcao`, `coleta` ou `recepcao_coleta` — só
> `admin`/super admin continuam vendo o botão, nas duas telas (por-fila e
> por-Local). O botão "Chamar" individual por linha, na tabela "Senhas em
> espera" (`painel_fila.html` apenas — `painel_local.html` nunca teve essa
> coluna), **não foi alterado** por essa decisão (fora do escopo do pedido).

Mudança só de template (`{% if current_user.is_super_admin or
current_user.tem_perfil('admin') %}` em volta do botão) — nenhuma mudança de
RBAC no backend; as rotas `chamar`/`chamar_local` continuam aceitando
requisições de qualquer perfil operacional autorizado por
`exigir_acesso_setor`, exatamente como antes.

> **REVERTIDO** (mesma sessão de trabalho, com uso real do sistema): o
> cliente reportou que recepção/coleta precisam chamar senhas diretamente —
> é o próprio trabalho delas — e pediu pra reativar o botão "Chamar" pra
> todos os perfis. O `{% if %}` acima foi removido de `painel_fila.html` e
> `painel_local.html`; o botão volta a aparecer normalmente pra
> recepcao/coleta/recepcao_coleta/admin, como era antes desta seção 10.16.
> RBAC de backend nunca mudou em nenhuma das duas versões. Teste antigo
> (`test_regressao_botao_chamar_oculto.py`) removido; substituído por
> `tests/test_regressao_botao_chamar_visivel.py`, que cobre o comportamento
> atual (botão visível pra todos os perfis operacionais).

> **ESCLARECIDO / ESTADO FINAL** (mesma sessão, print com destaque em
> vermelho): o que o cliente queria de volta não era o botão principal
> (seleção automática/rodízio) e sim um "Chamar" **por linha**, na tabela
> "Senhas em espera" — pra escolher manualmente qual senha específica
> chamar. Esse recurso já existia em `painel_fila.html` (coluna "Ações")
> desde sempre, mas **nunca existiu em `painel_local.html`** — por isso o
> botão principal precisou ficar visível pra todo mundo na correção
> anterior: sem o botão por linha, era a ÚNICA forma de chamar algo na tela
> por-Local. Correção final: `painel_local.html` ganhou a mesma coluna
> "Ações"/botão por linha de `painel_fila.html` (form com
> `codigo=s.codigo` oculto, POST em `chamar_local`, mesmo bloqueio de
> "Finalize o atendimento atual..." do botão principal) — com essa paridade
> resolvida, o botão principal (`#botao-chamar`) volta a ficar restrito a
> admin/super admin nas DUAS telas (estado idêntico ao desta seção 10.16
> original). Teste `test_regressao_botao_chamar_visivel.py` removido;
> substituído por `tests/test_regressao_botao_chamar_por_linha.py` (9
> casos), que cobre o estado final: botão principal oculto pra
> recepcao/coleta/recepcao_coleta (visível só pra admin) e botão por linha
> visível pra todos, nas duas telas.

---

## 10.17. ADENDO — Controle de atendimento: bloqueio de chamada sem finalização + recuperação de estado

> Pedido do cliente, com base nas seções 10.2 e 10.8: (1) enquanto o operador
> tiver uma senha `chamada`/`em_atendimento` em andamento, não deixar chamar
> a próxima antes de finalizar; (2) reabrir a tela já com o atendimento em
> curso depois de um reload/crash, sem o painel/TV re-disparar o áudio; (3)
> destacar visualmente quando há atendimento em andamento.

**1. `senha_atual_no_local()` passou a ser escopada por operador, não só por
Local** (`app/atendimento/helpers.py`) — a query pedida literalmente:
```sql
SELECT * FROM senhas WHERE operador_id = <usuario_logado>
  AND local_id = <local_atual> AND status IN ('chamada','em_atendimento')
ORDER BY chamada_em DESC LIMIT 1
```
Antes filtrava só por `local_id` + `status='chamada'` — fechava um caso
latente: uma senha deixada "chamada" por um operador anterior que nunca
finalizou (ex.: caiu a conexão) podia aparecer como "a senha atual" de um
operador novo que passasse a ocupar o mesmo Local depois (mesmo já não sendo
mais possível dois operadores ocuparem o mesmo Local ao mesmo tempo, adendo
10.15 — isso não impede uma senha **órfã** de uma ocupação anterior). Essa é
a MESMA função usada tanto pra popular a tela ("Senha chamada") quanto pra
decidir o bloqueio — o que trava a próxima chamada é sempre exatamente o que
está visível na tela.

**2. Bloqueio, reforçado no servidor (não só escondendo botão):** `chamar()`
e `chamar_local()` (`app/atendimento/chamada.py`) checam
`senha_atual_no_local(...)` antes de travar/selecionar qualquer coisa; se
houver senha em andamento, recusam com "Finalize o atendimento atual antes
de chamar o próximo." — vale pro botão principal **e** pro individual por
código, e pras duas variantes (por-fila e por-Local). Tocar/Realocar/
Finalizar/Aguardar continuam ativos normalmente (agem sobre a senha atual).
Depois de "Aguardar" (status volta pra `aguardando`), o Chamar libera
sozinho — nenhuma lógica de exceção extra, é só o efeito natural do status
mudar.

**3. Recuperação de estado — já era, na prática, a arquitetura existente:**
como `painel_fila`/`painel_local` (GET) sempre leem o estado atual do banco
via `senha_atual_no_local()`, um reload já reabria com "Local"/"Senha
chamada"/"Nome" preenchidos, sem precisar de nenhum código novo além do
escopo por operador do item 1. **O painel/TV nunca re-dispara o evento
`chamada` (áudio) num reload:** só as rotas de ação
(`chamar`/`chamar_local`/`tocar`/`talvez_atualizar_nome`) chamam
`emitir_chamada()` — as rotas GET (`painel_fila`, `painel_local`,
`dados_fila`, `dados_local`) nunca chamam, confirmado por teste que conecta
um cliente WebSocket na sala da fila e prova que reloads repetidos não geram
nenhum evento novo ali.

**4. Indicador visual:** badge "Em atendimento" + borda âmbar de destaque
(`.senha-chamada.em-atendimento`) no card "Senha chamada", nas duas telas,
tanto no render inicial quanto atualizado via WebSocket.

**Testes:** `tests/test_regressao_bloqueio_chamada_recuperacao_estado.py`
(18 casos) cobre bloqueio (principal, individual, por-Local, e a liberação
automática após Aguardar), status `em_atendimento` bloqueando igual a
`chamada`, recuperação de estado sem re-disparo de áudio (3 reloads seguidos
+ `/dados`), reload sem senha em andamento, indicador visual, e isolamento
por operador (senha de um operador nunca aparece/bloqueia outro).

---

## 10.18. ADENDO — Encerramento automático das sessões de atendimento (23h)

> Pedido do cliente: a clínica fecha antes das 23h, então nunca há atendimento
> genuíno em curso nesse horário — o sistema deve avisar com antecedência e
> encerrar sozinho as sessões de operador, cancelando qualquer senha ainda
> aberta, sem precisar de Celery/APScheduler/fila de tarefas.

**1. Configuração** (`.env`, lidas em `app/config.py`):
```
HORARIO_ENCERRAMENTO_SESSOES=23:00
AVISO_ENCERRAMENTO_MINUTOS=5
```

**2. Aviso antecipado + redirecionamento — client-side** (`atendimento/
_layout.html`, herdado por todas as telas de Atendimento): um `setInterval`
de 1s compara a hora local do navegador com o horário configurado. A partir
de `AVISO_ENCERRAMENTO_MINUTOS` antes, mostra um card fixo **não bloqueante**
(canto da tela, não cobre nav/conteúdo) com contagem regressiva
`M:SS`; o botão "Entendi" só esconde o card — a contagem e o redirecionamento
continuam rodando por trás. No horário exato, redireciona pra `/logout`.
Valores injetados via `atendimento_bp.context_processor`
(`_injetar_config_encerramento`, `app/atendimento/__init__.py`), sem precisar
passar por `render_template` em cada rota.

**3. Backup server-side** (`app/middleware/encerramento.py`, `before_request`
global): cobre o caso de a aba estar em background e o JS não ter disparado.
A cada requisição, se `datetime.now().time() >= HORARIO_ENCERRAMENTO_SESSOES`:
- Cancela toda `Senha` com status `chamada`/`em_atendimento` (→ `cancelada`,
  nunca `DELETE` — seção 8), gravando `LogAtendimento(evento=
  'encerramento_automatico_sistema', usuario_id=None)` por senha cancelada -
  novo valor no `ENUM` `evento_log` (migration `c3d9f4a1e6b2`, mesmo padrão
  já usado pra `realocado`/`voltou_aguardar` na migration `235bf852e44c`).
  Roda pra todas as empresas de uma vez (faxina de fim de expediente do
  sistema, não uma ação por-tenant); idempotente e sem flag "já rodei hoje" -
  a query já filtra pelos dois status, então rodar de novo sem nada aberto é
  um no-op barato.
  **Complementado pelo 10.77:** este gatilho é só por relógio e não tocava
  `aguardando` - senhas de dias anteriores ficavam abertas para sempre. O
  10.77 acrescenta a varredura por DIA da clínica (1º request de cada dia,
  a qualquer hora, inclusive `aguardando` -> evento `expirada_aguardando`) e
  o UPDATE condicional também neste cancelamento das 23h.
- Se a requisição é numa rota do blueprint `atendimento` e há um usuário
  autenticado: limpa `local_atual_id`/`local_desde`, `logout_user()`,
  `session.clear()`, flash informativo, redireciona pro login.

**4. Escopo do encerramento forçado:** só as telas de **Atendimento**
(`atendimento_bp`) derrubam a sessão — Totem (`/totem/<slug>`) e painel de TV
(`/painel/tv/<slug>`) são públicos, sem sessão de operador, e continuam
funcionando normalmente mesmo depois do horário (usam layouts próprios,
sem o script de aviso). **Admin também não é derrubado** — configurar o
sistema depois das 23h não é "atendimento em curso" (decisão explícita,
já que o pedido fala especificamente de telas de atendimento/operadores).

**Testes:** `tests/test_regressao_encerramento_automatico.py` (11 casos)
cobre: dados corretos embutidos no HTML pro JS (horário/minutos/URL de
logout), tela abrindo normalmente antes do horário, redirecionamento +
sessão de fato encerrada no/após o horário, cancelamento das senhas abertas
com log de auditoria (e que `finalizada`/`aguardando` não são tocadas),
idempotência, e confirmação de que totem/TV/admin não são afetados. Mesma
limitação de infraestrutura já registrada em testes anteriores desta sessão
(sem runtime de navegador, não dá pra exercitar o `setInterval`/contagem
regressiva de fato rodando - só que os dados corretos chegam até o JS).

---

## 10.19. ADENDO — Tela "Iniciar Turno" (configuração de turno pelo operador)

> Pedido do cliente, com print de referência de um sistema concorrente (tela
> "Alocar fila"): o operador deve poder escolher, ele mesmo, as filas (e o peso
> de cada uma no rodízio) e o Local do seu turno logo após o login, antes de
> acessar a tela de atendimento — sem depender de um admin pré-configurar o
> rodízio do Local (adendo 10.8).

**1. Fluxo:** `Login → Iniciar Turno (/atendimento/iniciar-turno) → Atendimento`.
Após o login, perfis puramente operacionais (`recepcao`/`coleta`/
`recepcao_coleta`, sem `admin`/`gerencial`, checado por
`auth/routes.py::_deve_ir_direto_para_turno`) são redirecionados direto pra
`iniciar_turno` em vez do dashboard (`core.index`) — quem também é
admin/gerencial/super admin continua caindo no dashboard como antes, já que
usa o sistema para mais do que atendimento.

**2. Tela "Iniciar Turno"** (`GET/POST /atendimento/iniciar-turno`, RBAC
`roles_required("recepcao", "coleta", "recepcao_coleta", "admin")` — mesma
lista `PERFIS_OPERACIONAIS` de `atendimento/filas.py`; `gerencial`/sem
perfil recebem 403):
- **Filas** (múltipla, uma por vez, via JS sem reload): dropdown de
  `filas_acessiveis()` + campo "Quantidade de chamadas seguidas" (padrão 0) +
  botão "Adicionar fila" — a lista adicionada mostra ordem/nome/peso e um "×"
  pra remover. A mesma fila pode ser adicionada mais de uma vez (só pra
  ajustar o peso dela) — o servidor **mescla** entradas repetidas em uma única
  (soma de `chamadas_seguidas`) antes de salvar (`_mesclar_filas_config`,
  `app/atendimento/turno.py`), evitando `fila_id` duplicado no motor de
  rodízio (`_proximo_item_na_rotacao` identifica "a vez" pelo `fila_id`, não
  pela posição — duplicata seria ambígua).
- **Local** (único, obrigatório): dropdown de `locais_acessiveis()`. Mesma
  regra de "um Local = um operador por vez" já usada em `escolher_local`
  (bloqueia com mensagem clara se outro operador já o ocupa).
- **Botão "Iniciar atendimento":** valida ≥ 1 fila e um Local válido; grava
  `TurnoAtivo` (upsert por `usuario_id`), ocupa o Local exatamente como
  `escolher_local` já fazia (`current_user.local_atual_id`/`local_desde`,
  `session[CHAVE_SESSAO_LOCAL]` — mantém compatibilidade com a tela por-fila
  legada e com a tela administrativa de ocupação de locais), e redireciona
  pra `painel_local` (a tela unificada por-Local do adendo 10.8).

**3. Turno já ativo → pula a tela** ~~(SUPERSEDIDO pela 10.23 - GET em
`iniciar_turno` deixou de pular pra `painel_local` mesmo com turno ativo,
sempre mostra o formulário; ver seção 10.23 pro texto completo da reversão.
Texto original mantido abaixo só como registro histórico do motivo original
da tela)~~: GET em `iniciar_turno` com um `TurnoAtivo.ativo=True` do operador
redireciona direto pra `painel_local` — tanto no primeiro acesso do dia
quanto após reload/crash (recuperação de estado, mesmo princípio do adendo
10.11: o estado vive no banco, não só na sessão do navegador).
`?reconfigurar=1` (link discreto "Reconfigurar turno" nas telas de
atendimento, ao lado de "Trocar local") contorna esse pulo e mostra o
formulário pré-populado com a configuração atual, sem deslogar - também
removido na 10.23 (deixou de ter efeito, já que o GET simples já faz isso
sempre).

> **Correção pontual (mesma sessão desta nota):** o link "Ir para atendimento
> (chamada de senhas)" em `core/index.html` apontava pra
> `atendimento.listar_filas` (a tela "Filas" legada, pré-esta seção) - achado
> ao investigar um pedido pra esconder o link "Filas" do menu de
> `atendimento/_layout.html` pra atendente/executor/atendente_executor
> (permanece visível só pra admin, rota sem RBAC novo). Corrigido pra
> `atendimento.iniciar_turno` - MESMO destino único que `login()` já usa pra
> estes perfis (item 1 acima), sem nenhuma lógica condicional de `TurnoAtivo`
> (confirmado explicitamente com o cliente antes de implementar: replicar a
> ideia "pula se já tem turno" reintroduziria, só neste link, o comportamento
> que a 10.23 removeu de propósito do fluxo de login). Teste em
> `tests/test_regressao_menu_filas_atendimento.py`.
>
> **Segunda ocorrência do mesmo bug, achada depois em uso real:** o item
> "🎫 Atendimento" do nav em `core/dashboard.html` (visível no Dashboard,
> perfis atendente/executor/atendente_executor/admin) tinha o MESMO problema
> - apontava pra `atendimento.listar_filas`. Corrigido pro mesmo destino
> (`atendimento.iniciar_turno`), mesmo padrão, sem lógica condicional nova.
> Grep final em todo o projeto (`atendimento.listar_filas`) confirmou que só
> sobrou o link "🎫 Filas" (restrito a admin/super_admin, item 1 acima) e o
> uso interno da própria rota (`app/atendimento/filas.py`, redireciona pra
> si mesma após salvar) - `app/atendimento/local.py::escolher_local` também
> tem uma referência (fallback quando o Local escolhido não tem rodízio
> configurado), mas é lógica de negócio legítima do fluxo legado
> `escolher_local`, não um link de menu com destino desatualizado - fora do
> escopo desta correção, não alterado.

**4. Encerrar turno** (`POST /atendimento/turno/encerrar`, botão "Encerrar
turno" nas telas de atendimento, ao lado de "Sair"): marca
`TurnoAtivo.ativo = False`, libera o Local (`local_atual_id = None`) e volta
pra `iniciar_turno` — **sem deslogar** (`logout_user()` não é chamado); o
operador pode iniciar outro turno em outro Local/filas sem logar de novo.

**5. Persistência — tabela `turnos_ativos`** (migration `d53cf46b3068`):
`id, usuario_id (FK usuarios, único), local_id (FK locais), filas_config
(JSON: lista de {fila_id, ordem, chamadas_seguidas}), iniciado_em, ativo`.
Uma linha por usuário (`unique` em `usuario_id`) — reiniciar o turno
atualiza a mesma linha, sem acumular histórico de turnos passados.
`app/middleware/encerramento.py` também marca `ativo=False` ao forçar logout
às 23h (adendo 10.18), reaproveitando o mesmo campo.

**6. Motor de rodízio ciente do turno** (`app/atendimento/helpers.py`):
`fila_ids_do_local`/`selecionar_proxima_fila_e_senha`/`senhas_aguardando_local`
ganharam um parâmetro `operador` opcional — quando o operador tem um
`TurnoAtivo` para o Local em questão, a configuração do turno **prioriza**
sobre o rodízio admin-configurado (`Local.rodizio_ordenado`, adendo 10.8) só
pra decidir a rotação daquele operador; sem turno ativo (ou parâmetro
omitido — compatibilidade com todas as chamadas antigas), comportamento
idêntico a antes. Isso permite ao operador montar seu próprio rodízio num
Local que nunca teve `LocalRodizioFila` nenhum configurado por um admin.

**Testes:** `tests/test_regressao_turno_ativo.py` (22 casos) cobre:
configuração com múltiplas filas (incl. a mesma fila duas vezes → mesclada em
uma entrada de peso somado), bloqueio ao tentar iniciar sem nenhuma fila e
sem Local, recuperação de turno ativo após reload/nova sessão (login →
`iniciar_turno` → pula direto pro Local salvo), encerramento de turno sem
deslogar (sessão continua autenticada, `TurnoAtivo.ativo=False`), Local já
ocupado bloqueando o início do turno, RBAC (gerencial recebe 403), e que o
motor de "Chamar" de fato usa a configuração do turno (não é só decorativo)
num Local sem nenhum rodízio admin-configurado.

---

## 10.20. ADENDO — "Minhas filas" (acesso rápido) no bloco Redirecionar (seções 10.2/10.10/10.19)

> **SUPERSEDIDO pela seção 10.29** — a seção "Minhas filas" descrita abaixo
> foi removida a pedido do cliente (não é correção de bug). O restante desta
> seção documenta a implementação original só para referência histórica; ver
> 10.29 pra decisão/estado atual. A busca livre (item 3 abaixo, adendo 10.10)
> continua existindo normalmente — não foi afetada pela remoção.

> Pedido do cliente: combinar dois modos de seleção de destino no bloco
> Redirecionar — botões de acesso rápido pras filas do turno ativo do
> operador (seção 10.19), além da busca livre já existente (adendo 10.10) —
> com um único estado de seleção compartilhado entre os dois modos.

**1. Seção "Minhas filas"** (`app/templates/atendimento/_redirecionar_busca.html`,
renderizada ANTES da busca): um botão por fila do turno ativo do operador
**para este Local**, na mesma ordem em que ele as organizou na tela "Iniciar
Turno". Cada botão mostra o nome da fila + um indicador `●` verde
("Atendendo agora") ou cinza ("Fora do horário") — mesmo `Fila.aberta_em()`
já usado pela busca. Clique seleciona aquela fila como destino imediatamente
(substitui a seleção atual, se houver) — sem submeter o form (`type="button"`).
Grade responsiva (`display: flex; flex-wrap: wrap`), cada botão do tamanho do
próprio texto (sem largura fixa) — não polui a tela mesmo com 15+ filas.
Alvos de toque ≥ 44px, sem interação hover-only (seleção é só por clique).

**2. Fonte dos dados — `filas_rapidas_redirecionar()`**
(`app/atendimento/helpers.py`): SEMPRE um subconjunto de `filas_destino()` —
nunca amplia quem pode ser destino do redirecionamento (mesmo isolamento
multi-tenant e mesma restrição opcional de `Local.filas_destino_permitidas`
já aplicados por `filas_destino()`); só reordena/destaca, pra acesso de um
clique, as filas que o próprio operador já escolheu no turno ativo daquele
Local especificamente (`TurnoAtivo.local_id == local.id` — turno de OUTRO
Local não aparece). Uma fila do turno que não seja mais um destino válido
(ex.: desativada, ou fora de `filas_destino_permitidas`) simplesmente não
vira botão — sem erro.

**3. Busca livre — inalterada** (adendo 10.10, mesmo componente/lógica):
continua listando TODAS as filas de `filas_destino()`, com o mesmo filtro
100% local em JS e agrupamento "atendendo agora" primeiro.

**4. Estado de seleção único e compartilhado:** os dois modos escrevem no
MESMO `#fila-destino-id` (campo que o backend do Realocar já lia, sem
mudança nenhuma). Selecionar via busca desmarca o botão rápido ativo (se
houver) e vice-versa — uma função `selecionar(fila)` central, chamada pelos
dois modos, mantém os dois lados sincronizados
(`sincronizarBotoesRapidos()`). O indicador de seleção atual (reaproveita o
`#selecionado-redirecionar` já existente, texto mudado pra "→
Redirecionando para: **[fila]**") aparece igual não importa qual modo foi
usado pra chegar ali.

**5. Degradação elegante:** operador sem turno ativo (ou com turno de outro
Local) — `filas_rapidas` vem vazia, a seção "Minhas filas" e o separador
visual simplesmente não renderizam (`{% if filas_rapidas %}`); só a busca
livre aparece, comportamento idêntico ao de antes desta seção.

**6. Backend do Realocar — sem nenhuma mudança:** `realocar`/`realocar_local`
(`app/atendimento/chamada.py`) continuam lendo `fila_destino_id` do form e
validando contra `filas_destino()`, exatamente como antes — só a UI de
seleção ganhou um segundo modo de preencher o mesmo campo.

**Testes:** `tests/test_regressao_redirecionar_minhas_filas.py` (13 casos)
cobre: botões na ordem do turno, indicador ativa/inativa por fila, fila fora
do turno não vira botão (mas continua na busca), turno de outro Local não
vaza, degradação sem turno ativo, Realocar funcionando de ponta a ponta com
o campo produzido pelos botões rápidos, e — confirmação explícita pedida
pelo cliente — um turno com 18 filas configuradas renderiza as 18 como
botão, sem nenhuma omitida, sem nenhum link "ver todas"/"mostrar mais", com
a grade `flex-wrap` presente e cada botão sem largura fixa (`flex: 0 0
auto`). Suíte completa de `test_regressao_redirecionar_busca.py` (13 casos,
adendo 10.10) revalidada sem nenhuma quebra.

> Nota: um botão de acesso rápido só existe pra uma fila se ela estiver no
> `filas_config` do turno ATIVO daquele operador (seção 10.19) — uma fila
> que apareça na busca livre mas não no turno configurado não é omissão/bug,
> é o "Minhas filas" refletindo fielmente o que o próprio operador escolheu
> ao iniciar (ou reconfigurar) o turno. Pra uma fila aparecer como botão
> rápido, ela precisa ser adicionada na tela "Iniciar Turno"/link
> "Reconfigurar turno".

---

## 10.21. ADENDO — Renome dos perfis "recepcao"/"coleta" para "atendente"/"executor"

> Pedido do cliente: renomear a NOMENCLATURA do perfil `coleta` para
> `executor` e do perfil `recepcao` para `atendente`. Confirmado com o
> cliente (pergunta de esclarecimento) que o escopo é só o **Perfil**
> (papel/RBAC do usuário) — o **setor** (área de trabalho usada nas URLs
> `/atendimento/recepcao|coleta/...`, em `Local.tipo` e em `Senha.setor`,
> todos ENUM no banco) permanece `recepcao`/`coleta`, sem nenhuma mudança —
> são conceitos distintos que só coincidiam de nome por convenção. O perfil
> combinado `recepcao_coleta` também foi renomeado, para
> `atendente_executor` (confirmado com o cliente).

**1. Migration de dados** (`f4900d785aee`, revisa `d53cf46b3068`) —
`perfis.nome` é um `VARCHAR(30)` simples (não ENUM), então é um `UPDATE`
de dados, não uma alteração de schema — as associações `usuario_perfil`
(por ID, não por nome) permanecem intactas, nenhum usuário perde acesso:
```sql
UPDATE perfis SET nome = 'atendente' WHERE nome = 'recepcao';
UPDATE perfis SET nome = 'executor' WHERE nome = 'coleta';
UPDATE perfis SET nome = 'atendente_executor' WHERE nome = 'recepcao_coleta';
```
(+ atualização da coluna `descricao` dos três, com `downgrade()` simétrico.)

**2. `seeds/seed_inicial.py`** — `PERFIS_PADRAO` atualizado com os novos
nomes, para que uma instalação nova (sem a migration acima ter rodado antes)
já nasça com a nomenclatura certa.

**3. RBAC no app** — todo `tem_perfil(...)`/`roles_required(...)` que
comparava contra os nomes antigos foi atualizado para os novos:
`app/atendimento/helpers.py` (`PERFIS_POR_SETOR` — só os VALORES, as
CHAVES continuam sendo o setor `"recepcao"`/`"coleta"`),
`app/atendimento/filas.py` (`PERFIS_OPERACIONAIS`),
`app/atendimento/local.py` (`_tem_acesso_operacional`),
`app/auth/routes.py` (`_deve_ir_direto_para_turno`, seção 10.19),
`app/templates/core/index.html`, `app/templates/atendimento/listar_filas.html`.
Nenhuma rota, URL, `Local.tipo` ou `Senha.setor` mudou — confirmado que
`app/admin/usuarios.py` já seleciona perfis por ID (não por nome), então a
tela administrativa de usuários não precisou de nenhuma mudança.

**4. Tela "Iniciar Turno" como inicial pro login de atendente/executor**
(reconfirmação do que já valia desde a seção 10.19): login de um usuário
com perfil puramente `atendente`/`executor`/`atendente_executor` (sem
`admin`/`gerencial`) já redirecionava direto para `/atendimento/iniciar-turno`
em todo logon — comportamento preexistente, só validado de novo com os
nomes de perfil atualizados (`tests/test_regressao_turno_ativo.py`, caso A).

**Testes:** todos os ~30 arquivos de teste que criavam
`Perfil(nome="recepcao"/"coleta"/"recepcao_coleta")` foram atualizados para
os novos nomes (perfil apenas — nenhuma URL/setor/`Local.tipo`/`Senha.setor`
tocado). Suíte completa revalidada sem nenhuma quebra.

---

## 10.22. ADENDO — UX de contexto multi-unidade no painel admin

> Pedido do cliente: super admin enfrentava confusão visual em múltiplas telas
> por ver locais/filas/botões de unidades diferentes misturados em listas
> planas, sem indicação de qual unidade cada item pertence.

**1. `Unidade.sigla`** (migration `0685df7c3a3d`, `VARCHAR(4)`, opcional):
prefixo curto pra distinguir itens de unidades diferentes. `Unidade.
sigla_exibicao` (property, só Python — nenhuma lógica no banco) cai nos 3
primeiros caracteres do nome, maiúsculas, quando a sigla não foi cadastrada —
nunca obrigatório preencher.

**2. Barra de contexto** (`app/templates/admin/_layout.html`, logo abaixo do
menu principal, só quando `empresa` está no contexto da página): dois selects
encadeados — **Empresa** (só super admin — admin de empresa nunca vê esse
select, já está implicitamente restrito à própria) e **Unidade** (opcional,
"Todas as unidades" = sem filtro, sempre visível pros dois papéis). Mudar a
seleção reenvia a MESMA página atual (`request.path`) com `empresa_id`/
`unidade_id` na querystring — reload leve, sem duplicar uma camada de filtro
em JavaScript por cima de cada listagem já renderizada no servidor.

**3. Persistência em sessão** (`app/admin/helpers.py::resolver_empresa_
contexto`/`resolver_unidade_contexto`, chaves `admin_contexto_empresa_id`/
`admin_contexto_unidade_id`): a escolha feita na barra de contexto vale pras
próximas páginas sem precisar reselecionar — só `empresa_id`/`unidade_id`
explícitos na querystring (vindos da própria barra, ou de qualquer link que
já os carregasse) atualizam a sessão; sem eles, a última escolha é
reaproveitada. Trocar de empresa limpa a unidade salva (uma unidade de uma
empresa não faz sentido pra outra). Um `unidade_id` que não pertence à
empresa atual (manipulação de querystring, ou resquício de sessão após trocar
de empresa) é **sempre ignorado com segurança** — cai em "sem filtro", nunca
filtra/vaza dado de outro tenant (`resolver_unidade_contexto` sempre valida
`Unidade.empresa_id == empresa.id`). Isolamento multi-tenant nas QUERIES em
si nunca mudou — o filtro de contexto é só um `WHERE unidade_id = X`
adicional, opcional, por cima do `WHERE empresa_id = Y` que já existia.

**4. Filtro por unidade nas listagens**: `locais`, `filas`, `usuários`
(`Usuario.unidades.any(...)`, já que é M:N), `totens` e `TVs` passam a
respeitar a unidade de contexto. `tipos_senha` (não tem `unidade_id` -
conceito de empresa inteira) e `unidades` (não faz sentido filtrar a própria
listagem por uma unidade) não são afetadas — a barra de contexto continua
aparecendo nelas (pro breadcrumb/navegação), só sem efeito de filtro.
`Botões do totem` já agrupava por unidade (tabela por unidade, com `<h2>`) -
a unidade de contexto agora só restringe esse agrupamento a uma única
unidade, quando escolhida.

**5. Breadcrumb "Empresa › Unidade › Nome da tela"** (`app/templates/admin/
_breadcrumb.html`, `{% include %}`): padronizado em TODA tela admin que tenha
`empresa` no contexto, substituindo os ~18 `<p class="empresa-tag">Empresa:
X</p>` duplicados que existiam antes (um por template). Reaproveita
`self.titulo()` do próprio `{% block titulo %}` de cada template — mas como
`self` dentro de um `{% include %}` referencia o PARTIAL incluído, não quem
inclui, cada template calcula `{% set nome_tela = self.titulo() %}` uma
linha antes do `{% include %}` (bug descoberto e corrigido durante esta
mesma implementação — sem esse `{% set %}`, `self.titulo()` dentro do
partial sempre lança `UndefinedError`).

**6. Prefixo "[SIGLA] Nome" + `<optgroup>` nos selects multi-unidade**: tela
"Iniciar Turno" (seção 10.19, filas e locais), Totem/TV no cadastro de Fila
(admin), Fila no cadastro de Botão do totem (admin), e o bloco Redirecionar
(seção 10.2/10.10/10.20, via JSON embutido — campo `nome_exibicao`, mantendo
`nome` intacto pra não quebrar o filtro de busca por texto digitado). Usa o
`groupby` nativo do Jinja (já ordena pela chave internamente) — nenhuma
lógica de agrupamento nova em Python. Selects que apenas ESCOLHEM uma única
unidade (ex.: "Unidade" no cadastro de Local/Totem/TV) não precisam de
prefixo/optgroup — o problema é só quando o select lista itens DE múltiplas
unidades ao mesmo tempo.

**Testes:** `tests/test_regressao_contexto_multi_unidade.py` (21 casos)
cobre: fallback de `sigla_exibicao`, persistência em sessão entre páginas,
filtro por unidade nas 5 listagens afetadas, troca de empresa limpando a
unidade salva, `unidade_id` de outra empresa nunca filtrando/vazando dado
(isolamento multi-tenant confirmado), admin de empresa vendo só o seletor de
Unidade (nunca o de Empresa), breadcrumb, e optgroup/prefixo nos 4 pontos
listados no item 6. Suíte completa (40 arquivos) revalidada sem quebras.

---

## 10.23. ADENDO — "Iniciar Turno" padronizada como tela de todo login (atendente/executor)

> Pedido do cliente: a tela "Iniciar Turno" (seção 10.19) deve ser SEMPRE a
> tela de entrada em todo login dos perfis `atendente`/`executor` (e
> `atendente_executor`) — reverte o "pular direto pra tela de atendimento
> quando já há turno ativo" da seção 10.19/item 3.

**O que muda:** `GET /atendimento/iniciar-turno` (`app/atendimento/turno.py::
iniciar_turno`) não redireciona mais direto pra `painel_local` quando o
operador já tem um `TurnoAtivo`. Passa a SEMPRE renderizar o formulário —
pré-populado com a configuração atual (filas + peso + Local) quando há turno
ativo, vazio quando não há. O operador confirma "Iniciar atendimento" pra
seguir adiante, mesmo que não vá mudar nada.

**O que NÃO muda:**
- O login continua redirecionando esses perfis pra `/atendimento/iniciar-turno`
  automaticamente (`auth/routes.py::_deve_ir_direto_para_turno`, inalterado).
- `TurnoAtivo`/recuperação de estado (adendo 10.11) continuam funcionando -
  é justamente o pré-preenchimento do formulário que "recupera" a
  configuração salva, agora sempre visível em vez de pulada silenciosamente.
- `POST /atendimento/iniciar-turno` (salvar/confirmar o turno) e
  `POST /atendimento/turno/encerrar` não mudaram.

**Limpeza:** o parâmetro `?reconfigurar=1` (que antes contornava o pulo
automático) deixou de ter qualquer efeito - removido do link "Reconfigurar
turno" (`painel_fila.html`/`painel_local.html`) e da rota, já que agora um
GET simples em `/atendimento/iniciar-turno` sempre mostra o formulário
pré-populado, comportamento idêntico ao que esse link já buscava.

**Testes:** `tests/test_regressao_turno_ativo.py` atualizado (seções F/G) -
GET sempre retorna 200 com o formulário (nunca mais 302), pré-populado com a
fila/local do turno ativo quando existente; recuperação após reload
confirmada pelo mesmo mecanismo. Suíte completa (40 arquivos) revalidada.

> **BUG CORRIGIDO** (mesma sessão de trabalho, uso real): deslogar ("Sair")
> não encerrava o turno - `TurnoAtivo.ativo` continuava `True`, então o
> PRÓXIMO login pré-populava "Iniciar Turno" com as filas/Local do turno
> ANTERIOR (parecia que o sistema "guardava" a configuração de um acesso pro
> outro). `auth/routes.py::logout()` passou a marcar o `TurnoAtivo` do
> usuário como inativo antes de derrubar a sessão - mesmo efeito do botão
> "Encerrar turno" (`turno.py::encerrar_turno`), aplicado automaticamente a
> todo logout explícito. Login volta a mostrar o formulário zerado depois de
> um "Sair" de propósito; a recuperação de estado (item acima) continua
> valendo normalmente pra reload/crash DENTRO da mesma sessão (sem logout
> explícito no meio). Teste novo em `test_regressao_turno_ativo.py` (seção
> K) - 23 casos no arquivo agora.

> **AJUSTE DE UX** (mesma sessão, pedido direto): o botão "Encerrar turno"
> (ao lado de "Reconfigurar turno", nas telas por-fila e por-Local) foi
> removido da interface para TODOS os perfis - agora que "Sair" (item
> acima) e todo login já cuidam de encerrar/zerar o turno automaticamente, o
> botão dedicado deixou de ser necessário. A rota `POST /atendimento/turno/
> encerrar` (`turno.py::encerrar_turno`) continua existindo no backend, sem
> nenhuma mudança de comportamento - só o botão que a acionava saiu do HTML
> (`painel_fila.html`/`painel_local.html`), junto com o CSS `.link-botao`
> que só era usado por ele.

---

## 10.24. ADENDO — Identidade visual GESTFILA (rebrand completo)

> Pedido do cliente: nova identidade visual completa - nome "GESTFILA",
> subtítulo, slogan, paleta de cores vermelha/cinza-escuro, logo tipográfico
> (sem imagem), aplicada em TODAS as telas (admin, atendimento, relatórios,
> login, totem, painel de TV), sem alterar nenhuma lógica de backend.

**1. Identidade:** nome **GESTFILA** ("GEST" em cinza escuro, "FILA" em
vermelho, peso 700), subtítulo "Sistema de Gestão de Atendimento e Filas
Virtuais", slogan "Atendimento organizado, rápido e inteligente." (visíveis
só na tela de login, junto do logo grande).

**2. Paleta como variáveis CSS globais** — `app/static/css/gestfila.css`
(arquivo novo, primeiro CSS externo do projeto - até então cada layout tinha
seu próprio `<style>` inline duplicado): `:root { --gf-primaria: #C0392B;
--gf-primaria-escura: #922B21; --gf-primaria-clara: #FADBD8; --gf-cinza-
escuro: #2C3E50; --gf-cinza-medio: #95A5A6; --gf-cinza-claro: #F4F6F7; --gf-
branco: #FFFFFF; --gf-sucesso: #27AE60; --gf-alerta: #E67E22; --gf-info:
#2980B9; }`. Todo `<style>` inline de cada layout passou a referenciar essas
variáveis em vez de cores soltas — manutenção futura da paleta num único
lugar, sem precisar caçar hex espalhado.

**3. Logo tipográfico** (`.gf-logo`/`.gf-logo-gest`/`.gf-logo-fila` em
`gestfila.css`) — puro HTML/CSS, sem imagem/arquivo externo, fonte sistema
(sem Google Fonts). Variante `.gf-logo--claro` troca "GEST" pra branco,
usada sempre que o logo aparece sobre fundo escuro (navbar dos painéis
admin/atendimento/relatórios, watermark do painel de TV) - sem essa
variante "GEST" (cinza escuro) ficaria invisível sobre um fundo da MESMA cor
(a navbar usa exatamente `--gf-cinza-escuro` de fundo).

**4. Favicon** — letra "G" vermelha sobre fundo branco, `<svg>` inline via
`data:image/svg+xml,...` (percent-encoded), sem arquivo externo. Presente em
todo `<head>` (`base.html`, `admin/_layout.html`, `atendimento/_layout.html`,
`relatorios/_layout.html`, `totem/_layout.html`, `painel/_layout.html`).

**5. Layout geral:** navbars (admin/atendimento/relatórios) com fundo
`--gf-cinza-escuro`, logo à esquerda, hover dos links em vermelho (borda
inferior). Botões primários vermelhos (hover vermelho escuro); secundários
brancos com borda cinza (hover cinza claro); ações destrutivas
(`button.link-perigo` - Excluir) em laranja (`--gf-alerta`), diferenciadas
do vermelho primário; toggles não-destrutivos (`button.link` - Ativar/
Desativar) em azul-info. Cards com borda sutil cinza-claro + sombra leve.
Tabelas: cabeçalho `--gf-cinza-claro`, linha em hover `--gf-primaria-clara`
(vermelho bem claro). Badges: Ativo=verde, Inativo=cinza, Em atendimento/
Prioridade=vermelho, Aguardando=azul, Finalizado=cinza escuro (`.badge.
aguardando`/`.badge.finalizado` definidas em `gestfila.css` mesmo sem uso
atual em nenhuma tela, pra já existir quando algum status futuro precisar).
Campos de formulário com borda vermelha + halo suave ao focar, em todo
layout (admin/atendimento/relatórios/base/totem).

**6. Ícones no menu** (Unicode/emoji, sem novo icon set): 🏠 Painel de
Senhas, 📊 Dashboard, 🎫 Filas/Tipos de senha, 🖥 Locais, 👨‍💼 Usuários, ⚙
Empresas/Unidades/Perfis, 🎯 Totens/Botões do totem, 🔔 Painel de Chamadas
(mapeado pro link já existente de "TVs" — cada TV É o painel de chamadas da
sua unidade, não existe uma rota "listar todos os paineis" separada),
📈 Relatórios.

**7. Login** (`base.html` + `auth/login.html`): novo bloco
`{% block cabecalho_marca %}{% endblock %}` em `base.html` (vazio por
padrão, só a tela de login o preenche) com logo grande + subtítulo + slogan
centralizados acima do formulário. Campos com foco vermelho, botão "Entrar"
vermelho full-width (já era o comportamento padrão de `button` em
`base.html`, sem precisar de CSS extra).

**8. Painel de TV** (`painel/_layout.html`) — mantido o fundo escuro
original (dark mode, adequado pra TV): `.linha-recente` (destaque da senha
recém-chamada) trocado de um vermelho solto (`#9c1f1f`) pra
`--gf-primaria`, o vermelho oficial da marca. Watermark "GESTFILA" discreto
(`.gf-watermark`, opacidade .35, canto inferior direito, `pointer-events:
none` - nunca compete com a informação real da tela nem intercepta clique/
toque).

**9. Totem** (`totem/_layout.html` + `totem/confirmar_acompanhantes.html`,
depois renomeado para `confirmar_geracao.html` na 10.34)
— trocado do tema escuro original pro gradiente claro branco→cinza-claro
pedido. Botões do totem vermelhos com `border-radius: 12px` e sombra leve;
variante `.navegacao` (submenus) em cinza escuro pra distinguir de "gera
senha"; `.botao-voltar` discreto (fundo cinza-claro, texto cinza escuro).
Logo GESTFILA centralizado no topo de toda tela do totem. Código da senha
gerada (`.senha-codigo`) em card branco com borda vermelha, número em
vermelho - troca do antigo amarelo (`#ffd166`) sobre fundo escuro.

**10. Templates alterados** (só CSS/HTML - nenhuma rota, query, formulário
ou lógica de backend tocada):
`app/static/css/gestfila.css` (novo), `app/templates/base.html`,
`app/templates/auth/login.html`, `app/templates/core/index.html`,
`app/templates/admin/_layout.html`, `app/templates/atendimento/_layout.html`,
`app/templates/atendimento/painel_fila.html`,
`app/templates/atendimento/painel_local.html`,
`app/templates/atendimento/iniciar_turno.html`,
`app/templates/atendimento/escolher_local.html`,
`app/templates/atendimento/_redirecionar_busca.html`,
`app/templates/relatorios/_layout.html`, `app/templates/totem/_layout.html`,
`app/templates/totem/confirmar_acompanhantes.html` (depois renomeado para
`confirmar_geracao.html`, 10.34), `app/templates/painel/_layout.html`.

**Confirmação:** suíte completa (40 arquivos de teste, cobrindo rotas,
formulários, WebSocket/Socket.IO, isolamento multi-tenant, RBAC) revalidada
sem NENHUMA quebra - as mudanças são puramente visuais (CSS/HTML), a
lógica de negócio nunca foi tocada. `GET /static/css/gestfila.css` confirmado
servindo 200 com as variáveis corretas.

---

## 10.25. ADENDO — Hardening de segurança (Fase 10): CSRF, rate limit, proxy confiável, páginas de erro

> Registro de funcionalidade que já estava implementada no código
> (`app/extensions.py`, `app/__init__.py`, `app/config.py`, `app/errors.py`)
> mas nunca tinha sido escrita como adendo — a Fase 10 da seção 9 só listava
> isso como entrega planejada ("Hardening: rate limit, logs de auditoria,
> testes, deploy"), sem confirmar o que de fato foi construído nem os
> detalhes de implementação.

**1. CSRF (Flask-WTF `CSRFProtect`)** — ativo globalmente
(`app/extensions.py::csrf`, `init_app` em `app/__init__.py`). Todo formulário
POST do painel administrativo/atendimento carrega
`<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
`WTF_CSRF_TIME_LIMIT = None` (sem expiração do token) é proposital: as telas
de Atendimento (`painel_fila`/`painel_local`) ficam abertas um turno inteiro
sem reload (seção 10.2/10.19) — o default de 1h do Flask-WTF expirava o
token renderizado no load da página e qualquer clique em
Chamar/Tocar/Realocar/Finalizar/Aguardar virava um "Bad Request" cru no meio
do turno. Sem limite de tempo o token só perde validade quando a sessão de
login termina (logout/expiração/logout remoto da seção 10.15) — a
`SECRET_KEY` + a sessão já bastam pra invalidar um token vazado de outra
sessão.

**2. Rate limit (Flask-Limiter, por IP)** — só no `totem_bp` (endpoint
público, sem login, alvo do "ataque de toque repetido" da seção 8):
`GET /totem/*` (navegação: seleção de serviço, confirmação de acompanhantes)
limitado a **60/min**; `POST /totem/*/gerar/<botao_id>` (geração da senha em
si, a operação sensível) limitado a **20/min**. Estoura o limite → página
429 própria (`errors/429.html`, "Muitas tentativas... aguarde um instante").
Nenhuma outra rota (admin/atendimento/relatórios, todas atrás de login) tem
rate limit — a superfície de risco real é só o totem público.

**3. `ProxyFix` + `TRUST_PROXY_HEADERS`** — pré-requisito pro item 2
funcionar corretamente atrás do Apache2/Nginx do deploy (seção 10): sem
confiar em `X-Forwarded-For`, `request.remote_addr` seria sempre o IP
loopback do proxy reverso, e todo cliente cairia no "mesmo IP" pro rate
limit (o limite viraria global, não por visitante). `TRUST_PROXY_HEADERS`
é `False` em `DevelopmentConfig`/`TestingConfig` (não há proxy na frente,
confiar nesses cabeçalhos sem um proxy real permitiria forjar o IP e burlar
o limite) e `True` só em `ProductionConfig`, com `ProxyFix(x_for=1, x_proto=1)`
(exatamente 1 hop confiável entre cliente e Gunicorn).

**4. Páginas de erro customizadas** (`app/errors.py`,
`app/templates/errors/{403,404,429}.html`, todas estendendo `base.html`):
403 (Proibido — RBAC/`tem_perfil`/tenant), 404 (Não encontrado) e 429
(rate limit do totem, item 2). Não há handler próprio pra 500 — cai no
comportamento padrão do Flask/proxy.

**5. Outros detalhes de sessão já ativos junto com o resto do hardening**:
`SESSION_COOKIE_HTTPONLY = True` e `SESSION_COOKIE_SAMESITE = "Lax"` em
todo ambiente; `SESSION_COOKIE_SECURE = True` só em produção (exige HTTPS,
inviável em dev local sem certificado); senha de usuário sempre com hash
via `werkzeug.security` (nunca texto puro), como já prescrito na seção 8.

**Nota para quem for escrever teste novo**: `TestingConfig` desliga CSRF
(`WTF_CSRF_ENABLED = False`) e rate limit (`RATELIMIT_ENABLED = False`) —
sem isso, testes de formulário/totem falhariam por token ausente ou 429
prematuro, não por bug real. Um teste que precise validar especificamente o
comportamento de CSRF/429 tem que sobrescrever essas duas flags no próprio
teste.

---

## 10.26. ADENDO — Redesign do painel de chamada (TV): card central, identidade visual, animação

> Pedido do cliente: modernizar o LAYOUT e a HIERARQUIA VISUAL do painel de
> chamadas (estilo aeroporto/atendimento hospitalar premium), SEM alterar
> nenhuma regra de negócio, endpoint ou lógica de tempo real já validada
> (WebSocket, salas, payload, TTS). Conduzido em duas sessões: Sessão A só
> diagnóstico (nenhum arquivo alterado); Sessão B em 5 partes incrementais
> (estrutura HTML/JS → identidade visual → animação → revisão de áudio/
> estado → testes), cada uma validada antes da próxima.

**Rota real confirmada (Sessão A):** `GET /painel/tv/<slug>` — a rota por
`<int:tv_id>` é só um redirect 302 legado, preservada sem mudança.

**1. Estrutura nova** (`app/templates/painel/tv.html`, reescrito): a tabela
de 2 colunas (Local | Senha) da especificação original (seção 6) foi
substituída por um card central "Chamando agora" (senha ou nome, `clamp()`
120–260px, limitado também por `vh` além de `vw` para não estourar em telas
mais baixas como 1366×768) + "Dirija-se ao" + local (`clamp()` 60–130px) +
lista lateral "Últimas chamadas" (até 5 itens, nunca duplicando a chamada em
destaque) + cabeçalho compacto com relógio/data client-side (nunca
formatado no servidor, mesma convenção das seções 10.11/10.12.2) e
indicador de conexão com 3 estados (verde=conectado, laranja/`--gf-alerta`
=reconectando, vermelho/`--gf-primaria-escura`=desconectado — a paleta
oficial não tem amarelo, por isso o laranja já existente foi reaproveitado
para esse estado).

**2. Chamada por múltiplas filas na mesma TV (decisão explícita, Sessão A):**
não existe (e continua não existindo) um conceito de "fila de exibição" —
cada fila da TV tem sua própria sala (`fila_<id>`); se duas filas chamam
quase ao mesmo tempo, chegam dois eventos `chamada` independentes e a
chamada mais recente sempre substitui a anterior no card (mesmo
comportamento de antes, decisão confirmada com o cliente: não implementar
uma fila client-side de exibição/áudio).

**3. Chamada por nome (`tipo: 'nome'`):** usa o MESMO card "Chamando
agora", só troca o conteúdo central (`valorPrincipal()` decide entre
`dados.senha` e `dados.nome_paciente`) — decisão explícita, sem variação
visual própria.

**4. `tipos_senha.cor` (seção 10.4):** no card em destaque, continua
preenchendo o fundo inteiro (`elChamadaAtual.style.backgroundColor`, igual
ao comportamento anterior). Na lista "Últimas chamadas" virou um indicador
discreto — borda esquerda colorida (`item.style.borderLeftColor`) em vez de
preencher o item inteiro, para manter o menor destaque visual pedido para
essa lista.

**5. Identidade visual — paleta 100% reaproveitada de `gestfila.css`**
(nenhuma cor nova introduzida): `--gf-primaria` (vermelho) é o destaque do
card ativo (borda + glow + selo do rótulo), mas o **número em si é branco**
— decisão de legibilidade a distância (número vermelho sobre fundo escuro
testa pior que branco puro numa TV vista de longe); `--gf-info` (azul) no
rótulo secundário "Dirija-se ao"; `--gf-alerta`/`--gf-sucesso`/
`--gf-primaria-escura` seguem exclusivos do indicador de conexão. Os tons de
fundo escuro do painel (nunca fizeram parte de `gestfila.css`, que é a
paleta do sistema administrativo) foram formalizados como variáveis próprias
(`--painel-fundo`, `--painel-fundo-header`, `--painel-fundo-elemento`,
`--painel-texto-secundario`) dentro do próprio `painel/_layout.html`, mesmos
valores de antes.

**6. Animação de nova chamada:** fade + leve zoom na entrada (450ms) +
glow/borda relaxando ao longo de ~3s até o estado de repouso — roda uma vez
só por chamada (nova ou "Tocar"), nunca em loop, e respeita
`prefers-reduced-motion`. Disparada **só** dentro de `inserirChamada()`
(evento `chamada` ao vivo) — nunca em `recarregarHistorico()` (reconexão/
reload), pelo mesmo princípio já validado para o áudio na seção 10.13 (nada
de efeito "de chamada nova" só porque a TV recarregou).

**7. As 3 correções da seção 10.13, preservadas e testadas sob a estrutura
nova:** `repetir: true` (Tocar) só re-renderiza o card, nunca duplica na
lista; `historico_chamadas_tv`/`entrar_tv` continuam reconstruindo o
histórico do banco a cada (re)conexão, sem nenhuma mudança de contrato;
`vozPtPreferida()` copiada sem alteração de lógica.

**8. `painel/indisponivel.html` (TV desativada) não foi tocado** — o novo
`border-bottom` vermelho do cabeçalho foi aplicado só à classe `.cabecalho`
(exclusiva de `tv.html`), nunca ao seletor genérico `<header>` que essa tela
também usa.

**9. Decisões sinalizadas, não implementadas nesta rodada** (ver
transcript da Sessão A/B para o contexto completo):
- Horário na lista "Últimas chamadas": o payload do WebSocket
  (`_payload_chamada`) não carrega timestamp hoje; mudar esse contrato
  ficou fora do escopo aprovado (só CSS/HTML/JS). A lista mostra só
  senha/nome + local, sem horário.
- Mensagem institucional do rodapé: texto fixo no template (sem campo novo
  no banco) — torná-la configurável por empresa exigiria uma coluna nova +
  tela admin.
- `Tv.layout` (campo de texto livre já existente no cadastro da TV, achado
  no diagnóstico da Sessão A, nunca lido em lugar nenhum antes nem depois
  deste redesign): decisão explícita de NÃO usá-lo como flag de rollout —
  o layout novo substitui o antigo direto, para todas as TVs.

**Arquivos alterados:** `app/templates/painel/tv.html` (reescrito),
`app/templates/painel/_layout.html` (CSS novo + variáveis de fundo
formalizadas). Nenhuma mudança em `app/painel/routes.py`,
`app/sockets/painel.py`, `app/atendimento/helpers.py`, models, ou
`painel/indisponivel.html`.

**Backup/rollback:** cópia dos dois arquivos originais (pré-redesign) em
`backups/painel_tv_20260727/` (`tv.html.bak`, `_layout.html.bak`) — restaurar
é copiar os dois de volta para `app/templates/painel/`.

**Testes:** `tests/test_regressao_painel_tv_redesign.py` (novo, 15 casos) —
cobre estrutura servida, não-disparo de áudio/animação em
reconexão/reload, não-duplicação no "Tocar", chamada por código/nome/tipo
prioritário via WebSocket real (payload intocado), múltiplas filas na
mesma TV, `prefers-reduced-motion`, `indisponivel.html` intocado, e ausência
de `innerHTML` com dado dinâmico. Suíte completa (38 arquivos
`test_regressao_*.py`) revalidada a cada parte da Sessão B, sem nenhuma
falha.

---

## 10.27. ADENDO — Correções pós-homologação do redesign do painel de TV (10.26)

> Teste manual em produção local (`/painel/tv/<slug>`) achou 4 problemas
> reais no redesign da seção 10.26, com evidência em screenshot. Corrigidos
> um de cada vez, cada um validado antes do próximo - mesmo princípio
> incremental já usado no resto do projeto.

**1. Card ficava vermelho em chamada comum (causa raiz confirmada por
consulta ao banco local, não suposição - nome do paciente omitido deste
registro por ser dado pessoal real, seção 8 do BLUEPRINT):** a senha do
teste (`id=70`) tinha `tipo_senha_id` apontando pro tipo
**"Normal"** (`peso_prioridade=0`, `Senha.prioridade=False`) - mas esse tipo
estava cadastrado com `cor='#FF0000'`. **Não era bug de código puro** - o
código sempre fez exatamente o que `_payload_chamada()` mandava (enviar
`cor` de qualquer `TipoSenha` que tivesse uma, sem checar prioridade); o
problema era o CADASTRO: alguém colocou vermelho num tipo que é o padrão
não-prioritário da clínica. Efeito prático idêntico a "vermelho virou
padrão", mas a causa era dado, não lógica solta.

Gate corrigido em `app/atendimento/helpers.py::_payload_chamada()`:
```python
if senha.prioridade and senha.tipo_senha is not None and senha.tipo_senha.cor:
    payload["cor"] = senha.tipo_senha.cor
```
**Gate definitivo: `Senha.prioridade == True`, sozinho - sem
`peso_prioridade` como condição alternativa.**

**Opção avaliada e descartada:** cogitou-se, no pedido original, um gate
`peso_prioridade > 0 OU Senha.prioridade == True`. **Descartada
deliberadamente** - não é uma pendência, não deve ser reaberta numa sessão
futura sem uma razão de negócio nova. Motivo: `peso_prioridade` (`TipoSenha`,
seção 10.4) é **peso de rotação/desempate**, não urgência da visita
individual - investigando `selecionar_proxima_senha()`, ele só é consultado
para decidir a ORDEM entre senhas que já são `prioridade=True` (ex.: "Idoso"
antes de "Gestante" quando os dois estão na fila prioritária ao mesmo
tempo). Nunca foi, em lugar nenhum do sistema, um sinalizador independente
de "esta chamada é prioritária" - usá-lo como critério alternativo (`OR`)
reintroduziria exatamente o bug do item 1 acima: um `TipoSenha` com
`peso_prioridade` configurado por engano (ou pra outro fim que não
prioridade) voltaria a pintar o card de uma senha `Senha.prioridade=False`.
`Senha.prioridade` é a única fonte de verdade sobre "esta chamada específica
é prioritária" hoje - a mesma flag que já decide FIFO-vs-rodízio.

Efeito colateral (aceito, não uma regressão): como `_payload_chamada()` é
reaproveitada por `historico_chamadas_tv()` e `registrar_toque()`, o gate
vale igual pra chamada ao vivo, "Tocar" e o histórico reconstruído por
reconexão - consistente nos três caminhos.

**2. Chamada por nome sem tratamento próprio:** usava o mesmo `clamp()` da
senha (120-260px), estourando a tela com nomes de 2+ palavras. Corrigido com
`tamanhoFonteNome()` (`painel/tv.html`) - 4 faixas de tamanho por
quantidade de caracteres do nome (não só o caso do teste manual), aplicadas
via `elChamadaValor.style.fontSize` + classe `.chamada-atual-valor--nome`
(permite quebra de linha, `word-break`/`hyphens`, que o modo "senha" não
usa). A capitalização "estranha" do nome real usado no teste manual (omitido
deste registro, dado pessoal - seção 8) **não era bug de CSS** - confirmado
que já vem gravada assim em `Senha.nome_paciente`
(texto livre digitado pelo operador, sem validação de capitalização - por
design, seção 10.2); não havia nenhum `text-transform` no código a
remover. Texto continua inserido via `textContent` (nunca `innerHTML`).

**3. Lista "Últimas chamadas" vazia com "1 chamada hoje" no rodapé:** não
era bug de integração com `historico_chamadas_tv()` - é o resultado
matemático esperado do design da 10.26 (a única chamada do dia ocupa o
card, a lista mostra só as OUTRAS, e com 1 chamada não sobra nenhuma outra).
Comportamento confirmado: uma chamada em andamento não deve aparecer
duplicada na lista. O bug real era a ausência de qualquer mensagem pra esse
estado especificamente - `atualizarMensagemListaVazia()` (`painel/tv.html`)
agora distingue "nada aconteceu hoje" ("Aguardando chamadas…") de "só teve
a de agora" ("Nenhuma chamada anterior hoje."), chamada nos dois fluxos
(chamada ao vivo e reconexão).

**4. Rodapé sobreposto pelo watermark:** `.gf-watermark` (`gestfila.css`,
`position: fixed; bottom: .75rem; right: 1rem`) ocupava o mesmo canto
inferior direito do texto "Última atualização" do rodapé. Corrigido com uma
sobrescrita só de `bottom` (`3.5rem`, acima da barra do rodapé) dentro do
próprio `painel/_layout.html` - **não** em `gestfila.css`, que é
compartilhado com o resto do sistema (`.gf-watermark` hoje só é usado nesta
página, mas a correção evita depender disso continuar verdade no futuro).

**Arquivos alterados:** `app/atendimento/helpers.py` (só o gate do item 1 -
primeira mudança deste redesign fora de `app/templates/painel/`),
`app/templates/painel/tv.html`, `app/templates/painel/_layout.html`.

**Backup/rollback deste round:** `backups/painel_tv_bugfix_20260727_193907/`
(`helpers.py.bak`, `tv.html.bak`, `_layout.html.bak`) - estado pós-correção
dos 3 arquivos. Distinto do backup da 10.26 (`backups/painel_tv_20260727/`),
que é do estado ANTES do redesign inteiro - não serve pra reverter só estas
4 correções sem perder o redesign todo junto.

**Testes:** `tests/test_regressao_painel_tv_redesign.py` foi de 15 para 25
casos - acrescenta cobertura específica dos 4 problemas (tipo não-prioritário
com cor cadastrada não pinta o card; nomes de 1 palavra/composto
acentuado/capitalização "como gravada"; histórico com 1 e com 3 chamadas
sequenciais; ordem de cascade do CSS do watermark). Suíte completa (38
arquivos) revalidada a cada correção, sem nenhuma falha.

**Limitação registrada:** confirmação visual final (tamanho de fonte do
nome em tela cheia, posição exata do watermark) não foi feita por
screenshot - sem acesso a navegador/renderização neste ambiente, só
validação via HTML/CSS servido + contrato de dado real (mesmo padrão de
teste do resto do projeto). Verificação visual em 1920×1080/1366×768 segue
pendente de confirmação manual.

---

## 10.28. ADENDO — Impressão do totem via window.print() do navegador (substitui a 10.6)

> **NOTA CRUZADA:** o layout do cupom (Fase 2 abaixo) foi corrigido depois, a
> partir de impressão física real (centralização + contraste + procedimento de
> impressão do totem) - ver **10.38**.

> Mudança de arquitetura consciente, não uma correção incremental: troca do
> mecanismo de impressão silenciosa via `win32print`/ESC-POS (adendo 10.6)
> por impressão via diálogo nativo do navegador (`window.print()`), com
> prévia visual do cupom antes de imprimir - pedido explícito do cliente,
> que queria poder ver o comprovante antes dele sair impresso.

**1. Prévia + impressão** (`totem/senha_gerada.html`): a tela que já
mostrava "Sua senha" (código em destaque) ganhou, abaixo, uma prévia visual
do cupom (nome da empresa, endereço/telefone da unidade, fila, código,
data/hora) e um botão explícito **"Imprimir comprovante"** - `window.print()`
só é chamado no clique desse botão, nunca sozinho ao carregar a tela. CSS
`@media print` isola só o cupom na hora de imprimir (`visibility: hidden`
em tudo, `visibility: visible` só no cupom, reposicionado pra ocupar a
página inteira) - evita o cabeçalho/rodapé do site aparecendo na folha
impressa.

**2. Largura do cupom - decisão deliberada de NÃO fixar em mm:** os totens
reais usam impressoras térmicas estreitas (uma Zebra ZD220 e uma impressora
de totem física), mas a largura de mídia instalada varia por equipamento/
configuração (a ZD220 comum varia entre 2"/3"/4" dependendo do rolo
instalado) - sem certeza de bater com os 58-80mm da Bematech usada como
referência na 10.6. Em vez de arriscar um valor fixo errado (ou adicionar
um campo novo de "largura do cupom" configurável por totem), o CSS do modo
impressão usa `width: 100%` + `@page { size: auto; margin: 0; }` -
quem decide o tamanho físico real da página é o **driver da impressora já
configurado no Windows daquele totem**, não a CSS da página web. A única
largura fixa no código (~320px) é só a da prévia NA TELA, puramente
estética, sem relação nenhuma com o tamanho real de impressão.

**3. Dados do cupom - novos campos em `Unidade`, não em `Empresa`:**
`endereco` (`Text`, nullable) e `telefone` (`String(30)`, nullable),
opcionais. Ficam em `unidades`, não em `empresas`, porque cada unidade é um
local físico distinto - uma mesma empresa pode ter várias unidades em
endereços diferentes - o cupom impresso no totem de uma unidade tem que
mostrar o endereço DAQUELA unidade, não um endereço genérico da empresa toda.
Migration `c53c321f3868` (`downgrade()` simétrico). Tela admin de Unidade
(`admin/unidades/form.html`) ganhou os dois campos, opcionais, sem quebrar
cadastro já existente.

**4. Mudança de responsabilidade operacional (sinalizada explicitamente,
não só um detalhe técnico):** antes, a impressora de cada totem era
configurada **centralizadamente** no cadastro do Totem (`nome_impressora_
windows`, editável por um admin de qualquer lugar pelo painel). Com
`window.print()`, cada totem passa a depender da **impressora padrão
configurada localmente no Windows daquela máquina específica** - ninguém
mais troca isso remotamente pelo GESTFILA; quem for trocar a impressora
física de um totem precisa mexer no Windows do PC daquele totem. Confirmado
que hoje existem 2 totens físicos cadastrados, em unidades/máquinas
diferentes.

**5. Mecanismo antigo desativado, não removido:** `app/totem/routes.py::
gerar_senha()` não chama mais `app/totem/impressao.py::
imprimir_senha_se_configurado()`. O módulo inteiro (`montar_cupom`,
`enviar_para_impressora_windows`, `win32print`/ESC-POS) continua existindo
no repositório, sem alteração - só parou de ser chamado - até a nova
abordagem estar validada em produção real. O campo `nome_impressora_windows`
(model `Totem`) também **não foi removido** do banco nem do cadastro admin -
fica documentado como não mais usado pela impressão em si (só resta como
metadado legado), sem migration de remoção por enquanto.

**6. Alerta obsoleto removido:** o dashboard gerencial (`relatorios/
queries.py::coletar_alertas`) tinha um alerta "Totem sem impressora
configurada" baseado em `nome_impressora_windows` estar vazio - removido,
porque ficaria incorreto (um totem pode imprimir perfeitamente bem sem
nunca ter preenchido esse campo, que não decide mais nada).

**7. Não-bloqueio preservado (era o requisito mais crítico a não regredir):**
a senha é gerada e commitada no banco pelo `POST /gerar` **antes** da tela
com a prévia sequer aparecer - cancelar/fechar o diálogo de impressão do
navegador, ou nunca tocar em "Imprimir", não tem como desfazer algo que já
aconteceu no servidor. A garantia não depende de nenhum código de
tratamento de falha (diferente da 10.6, que precisava de tolerância a falha
explícita para o mesmo efeito) - é uma consequência direta de onde a
prévia/impressão acontece no fluxo (depois, no client-side, não antes).
Retorno automático à tela inicial (`meta refresh`) mantido, só alongado de
10s pra 20s - dá espaço real pra abrir e fechar o diálogo nativo de
impressão antes de a tela trocar sozinha. **Nota posterior (fora desta
seção, sem adendo próprio):** o `meta refresh` foi trocado por um
`setTimeout` próprio em `app/templates/totem/senha_gerada.html`, pausado em
`beforeprint` e retomado em `afterprint` (`meta refresh` não tem gancho de
JS nenhum, continuaria contando por baixo do diálogo de impressão). Como o
diálogo passou a pausar a contagem inteira - os 20s só contam DEPOIS dele
fechar, nunca durante -, a folga deixou de ser necessária e o valor foi
reduzido pra 3s, padronizado com o resto do totem.

**Arquivos alterados:** `app/models/unidade.py` (+ migration
`c53c321f3868`), `app/admin/unidades.py`, `app/templates/admin/unidades/
form.html`, `app/templates/admin/_layout.html` (CSS de `textarea`),
`app/templates/totem/senha_gerada.html` (reescrito), `app/totem/routes.py`
(chamada removida + `fila` passada ao template), `app/relatorios/
queries.py` (alerta removido). **Não alterados:** `app/totem/impressao.py`,
model `Totem`/`nome_impressora_windows`, cadastro admin de Totem.

**Testes:** `tests/test_regressao_fase10_6.py` reduzido a cobertura
unitária do módulo legado + uma prova de que a rota não chama mais
`win32print` (11 casos). Novo `tests/test_regressao_cupom_impressao_
navegador.py` (7 casos) - conteúdo do cupom com/sem endereço-telefone,
`window.print()` só no clique, `@media print` isolando o cupom, geração
funcionando igual com/sem `nome_impressora_windows` preenchido, retorno
automático preservado. `tests/test_regressao_dashboard.py` ajustado (alerta
removido da lista esperada). Suíte completa (40 arquivos) sem falhas.

---

## 10.29. ADENDO — Remoção de "Minhas filas" no bloco Redirecionar (supersede 10.20)

> Remoção de funcionalidade a pedido do cliente, não correção de bug: a
> seção "Minhas filas" (adendo 10.20) — botões de acesso rápido às filas do
> turno ativo do operador, no bloco Redirecionar — foi considerada
> desnecessária tanto no fluxo do atendente quanto do executor e removida.

**1. Diagnóstico prévio — acoplamento identificado e confirmado com o
cliente antes da remoção:** `_redirecionar_busca.html` (o partial que
continha "Minhas filas") é compartilhado por `painel_fila.html` (seção 10.2)
**e** `painel_local.html` (adendo 10.8) — os dois route handlers
(`painel_fila()`/`painel_local()`, `app/atendimento/chamada.py`) chamavam
`filas_rapidas_redirecionar()` da mesma forma. O pedido original citava só
`painel_local.html`, mas como a 10.20 já documentava o recurso como
desenhado para as duas telas desde o início (título "seções
10.2/10.10/10.19"), o cliente confirmou remover das duas ao ser
apresentado o acoplamento, em vez de manter só numa tela via lógica
condicional nova.

**2. O que NÃO foi afetado (confirmado antes de remover, não presumido):**
- **Tela "Iniciar Turno" (10.19):** continua existindo normalmente —
  operador continua configurando filas/local do turno sem nenhuma mudança.
- **Rodízio automático entre filas por Local (10.8):** lógica 100%
  back-end (`selecionar_proxima_fila_e_senha()`/`_itens_rotacao()`,
  `app/atendimento/helpers.py`), estruturalmente independente do bloco
  Redirecionar — continua funcionando exatamente igual.
- **Busca livre por nome (10.10):** único modo de seleção de destino que
  resta no bloco Redirecionar, sem nenhuma mudança de comportamento —
  continua consumindo `filas_destino()` normalmente.
- **Atendente vs. executor:** `painel_local.html` é o mesmo template pros
  dois perfis (`setor=recepcao`/`coleta` só muda texto de rótulo e URLs de
  ação) — a remoção vale automaticamente para ambos.

**3. O que foi removido:**
- O bloco `{% if filas_rapidas %}...{% endif %}` (seção "Minhas filas" +
  separador visual "Buscar outra fila") de
  `app/templates/atendimento/_redirecionar_busca.html`, junto com o
  CSS (`.rotulo-secao-redirecionar`, `.grade-filas-rapidas`,
  `.botao-fila-rapida*`, `.divisor-secoes-redirecionar*`) e o JS
  específico (`elGradeRapidas`, `sincronizarBotoesRapidos()`, listener de
  clique da grade) exclusivos dela.
- A passagem de `filas_rapidas=filas_rapidas_redirecionar(...)` nos dois
  `render_template` (`painel_local()` e `painel_fila()`,
  `app/atendimento/chamada.py`), e o import correspondente.

**4. O que NÃO foi removido (código legado preservado, mesma convenção da
10.28 para `app/totem/impressao.py`):** `filas_rapidas_redirecionar()`
continua existindo em `app/atendimento/helpers.py`, funcional, só sem
nenhum call site ativo.

**Arquivos alterados:** `app/atendimento/chamada.py` (import + 2 chamadas
de `filas_rapidas_redirecionar` removidas), `app/templates/atendimento/
_redirecionar_busca.html` (seção "Minhas filas" + CSS/JS específicos
removidos, comentário de topo atualizado). **Não alterados:**
`app/atendimento/helpers.py` (função preservada), `painel_local.html`,
`painel_fila.html` (só consomem o partial, sem lógica própria do bloco).

**Testes:** `tests/test_regressao_redirecionar_minhas_filas.py`
reescrito (7 casos) — confirma que a seção não aparece mais em nenhuma das
duas telas mesmo com turno ativo configurado (cenário que antes a exibia),
busca livre continua funcionando nas duas, Realocar continua de ponta a
ponta via `fila_destino_id`, e `filas_rapidas_redirecionar()` continua
importável/funcional mas sem uso em `chamada.py`. Suíte completa (40
arquivos) sem falhas.

---

## 10.30. ADENDO — Escala responsiva de "Últimas chamadas" e rodapé no painel de TV

> Reportado pelo cliente numa TV física real: o card "Chamando agora" e a
> lista "Últimas chamadas"/rodapé ficavam desproporcionais entre si numa
> tela grande de verdade — o card já escalava bem, o resto não.

**Causa:** `.ultimas-chamadas h2`, `#lista-ultimas li`, `#lista-ultimas
.item-local` e `footer` (`app/templates/painel/_layout.html`) usavam
`font-size` fixo em `rem`, que não cresce com o tamanho físico da tela —
diferente do card `.chamada-atual-valor`/`.chamada-atual-local`, que já
usava `clamp()` com `vw`/`vh` desde o redesign (10.26). Numa TV grande
vista de longe, texto em `rem` fixo fica desproporcionalmente pequeno.

**Correção:** os quatro seletores acima passaram a usar `clamp()` com `vw`
(mesmo padrão já usado no card), e depois recalibrados por pedido explícito
do cliente: "Últimas chamadas" aumentada mais um pouco além do ajuste
inicial, "Chamando agora" (`.chamada-atual-valor`/`.chamada-atual-local`)
reduzido ~15-17% pra equilibrar a proporção entre os dois blocos.
Verificado localmente (servidor real + Chrome headless, viewport
1920×1080) — sem estouro de layout em nenhum dos ajustes. Confirmação
final de proporção depende da TV física real, não reproduzível neste
ambiente.

**Arquivos alterados:** `app/templates/painel/_layout.html`. **Testes:**
nenhum teste automatizado cobre valor exato de `font-size` (é ajuste visual
fino, sem contrato de dado) — suíte completa (40 arquivos) revalidada sem
quebras a cada rodada de ajuste.

---

## 10.31. ADENDO — Rótulos "Atendente"/"Executor" estendidos a `Local.tipo` e às telas de setor

> Pedido do cliente: estender a nomenclatura adotada na 10.21 (perfis
> `atendente`/`executor`) também aos rótulos visíveis de `Local.tipo` e das
> telas de Atendimento por setor — que antes continuavam mostrando
> "Recepção"/"Coleta" (decisão original da 10.21, que manteve *setor*
> deliberadamente separado de *perfil*). O cliente optou por unificar a
> terminologia visível ao usuário nessas telas também.

**Mudança é só de rótulo (texto exibido), não de dado:** os valores
internos `Local.tipo`/`setor` (`'recepcao'`/`'coleta'`, usados em URLs,
`Senha.setor` e toda a lógica de RBAC/`PERFIS_POR_SETOR`) **não mudaram** —
mesmo espírito da 10.21, sem migration, sem risco de rota quebrar. Só o
texto visível ao usuário foi trocado, nos seguintes pontos:

- `app/templates/admin/locais/listar.html` (coluna "Tipo" da listagem) e
  `app/templates/admin/locais/form.html` (`<select id="tipo">`) — telas de
  cadastro de Local.
- `app/templates/atendimento/painel_local.html` e `.../painel_fila.html` —
  título da página e subtítulo, que mostravam "Recepção"/"Coleta" derivado
  do `setor` da URL.
- `app/templates/atendimento/listar_filas.html` — links de acesso rápido ao
  painel de cada fila por setor.

**Nota de escopo:** o próprio `setor` continua `recepcao`/`coleta` nas URLs
(`/atendimento/recepcao|coleta/...`) — só descoberto/confirmado como
inalterado durante o pedido de remoção de "Minhas filas" (10.29), não
tocado aqui de novo. Nenhuma rota, ENUM de banco ou lógica de
autorização mudou.

**Testes:** nenhum teste da suíte afirmava o texto "Recepção"/"Coleta"
nesses pontos especificamente (os únicos usos de "Recepção"/"Coleta" em
testes eram nomes de dado - `Fila.nome`, `TotemBotao.label` - não
relacionados a este rótulo). Suíte completa (40 arquivos) revalidada sem
quebras.

---

## 10.32. ADENDO — Separação `Local.nome` em `nome_exibicao`/`identificador_admin`

> Pedido do cliente: separar o campo único `Local.nome` (guichês/postos,
> seção 10.3) em dois, mesmo espírito de um cadastro de usuário (nome de
> exibição vs. login) - motivado por ambiguidade real: nomes de Local podem
> se repetir entre unidades diferentes (ex.: "Guichê 01" em duas unidades),
> o que já causava confusão em telas administrativas onde o admin precisa
> identificar sem dúvida QUAL "Guichê 01" está escolhendo/vendo.

**Trabalho em 3 fases, todas concluídas na mesma sessão** (Fase 0 -
diagnóstico de impacto - relatada na conversa/commits, não repetida aqui).

### Fase 1 - Schema/migration

**1. Dois campos novos em `Local`** (`app/models/local.py`, migration
`5ba0cdce86cb`, revisa `c53c321f3868`):
- `nome_exibicao` (`String(150)`, obrigatório) - o que aparece para
  paciente/atendente (painel de TV, tela de Atendimento). Pode repetir
  entre unidades diferentes, sem constraint de unicidade nenhuma.
- `identificador_admin` (`String(200)`, obrigatório, **único por
  `empresa_id`** - `UniqueConstraint uq_locais_empresa_identificador_admin`)
  - usado só nas telas administrativas (listagem, Iniciar Turno, rodízio,
  ocupação).
- `nome` (campo antigo) **mantido intocado por enquanto** - será removido
  numa fase futura, só depois que a Fase 3 migrar 100% dos pontos de
  leitura mapeados (Fase 0, ver histórico da conversa/commits) - decisão
  deliberada de não fazer o "big bang" (schema + todos os reads) numa
  tacada só.

**2. Migração de dados - convenção uniforme, não só nos casos que colidem:**
valor inicial de `identificador_admin` = `"[SIGLA da Unidade] nome"` (usa a
mesma lógica de `Unidade.sigla_exibicao` - `sigla` cadastrada, ou fallback
pros 3 primeiros caracteres do nome da unidade em maiúsculas, adendo 10.22),
aplicado a **TODOS** os Locais existentes, não só aos que colidiam sem o
prefixo - decisão explícita do cliente, para nunca ter uma mistura de
Locais com/sem prefixo no mesmo sistema (o que reabriria o mesmo tipo de
confusão que a separação de campos tenta resolver). `nome_exibicao` recebe
o valor de `nome` sem nenhuma alteração.

**3. Desempate residual por id (defensivo - documentado aqui de propósito,
não só como comentário no código, para não ser confundido com bug no
futuro):** depois de aplicar o prefixo `"[SIGLA] nome"`, se AINDA sobrar
colisão case-insensitive dentro da mesma empresa (cenário não observado nos
dados reais nesta migration, mas matematicamente possível - ex.: duas
unidades cujos 3 primeiros caracteres coincidem quando nenhuma tem
`Unidade.sigla` cadastrada), o `id` do Local é anexado entre parênteses ao
final de **todos** os membros do grupo colidente (ex.: `"[MAT] Guiche 01
(7)"`) - nunca só nos "duplicados", pra o resultado ser simétrico e
previsível. **Se esse sufixo aparecer num `identificador_admin` real no
futuro, é o comportamento esperado deste script de migração, não um bug.**

**4. Verificação real antes de aplicar no banco de desenvolvimento:**
antes de rodar a migration no banco real (`painel_senha`), foi criada uma
cópia completa (`painel_senha_teste_migracao`, via `CREATE TABLE ... LIKE`
+ `INSERT ... SELECT` de todas as 21 tabelas) e a migration rodou primeiro
só nela. Achado real confirmado por consulta direta ao MySQL: a collation
de `locais.nome` é `utf8mb4_0900_ai_ci` (case+accent-insensitive,
confirmado com `SELECT 'Guiche 01' = 'GUICHE 01'` → `true`) - existiam 2
pares de Locais com o mesmo nome ignorando maiúsculas/minúsculas na mesma
empresa (`Guiche 01`/`GUICHE 01` e `Guiche 02`/`GUICHE 02`, cada par numa
unidade diferente), que teriam quebrado a constraint de unicidade se o
`nome` cru fosse copiado direto. O prefixo `[SIGLA]` resolveu os dois pares
sozinho (`[MAT] Guiche 01` × `[FIL] GUICHE 01` não
colidem mais) - conferido visualmente a lista completa dos 9
`identificador_admin` resultantes (sem espaçamento estranho, sem prefixo
duplicado) antes de considerar a estratégia validada. `downgrade()`
também testado na cópia (remove as colunas/constraint de volta,
simétrico). Cópia de teste removida depois da validação - a migration só
foi então aplicada ao banco real (`flask db upgrade`, revisão
`5ba0cdce86cb`).

Imediatamente após a Fase 1, a suíte completa ficou vermelha (29 arquivos) -
esperado e não revertido: qualquer `Local(...)` (produção ou fixture de
teste) sem os dois campos novos passou a violar `NOT NULL`. Confirmado com o
cliente antes de prosseguir: seguir direto para as Fases 2/3 na mesma sessão,
em vez de estabilizar com um valor provisório e parar.

### Fase 2 - Formulário de cadastro/edição (`app/admin/locais.py`,
`app/templates/admin/locais/form.html`)

**1. Dois campos no formulário**, substituindo o único "Nome" antigo:
"Nome de exibição (aparece para o paciente/TV)" e "Identificador
administrativo (único, usado só no cadastro)". `nome` (legado) é mantido em
sincronia com `nome_exibicao` em toda gravação (`novo_local`/`editar_local`),
não exposto no formulário.

**2. Sugestão automática de `identificador_admin` via JS**
(`_siglas_por_unidade()` no backend, mapa `unidade_id -> sigla_exibicao`
embutido no template): ao digitar o nome de exibição ou trocar a unidade,
o campo é preenchido com `"[SIGLA] nome de exibição"` - mas só enquanto o
operador não editar esse campo à mão (`identificadorEditadoManualmente`,
JS) e nunca em cima de um Local já existente sendo editado (evita
sobrescrever um valor real já customizado). Verificado via Chrome headless
simulando digitação: `"Guichê 05"` → sugestão `"[MAT] Guichê 05"`.

**3. Validação de unicidade com mensagem clara** (`_validar_local()`):
checagem case-insensitive de `identificador_admin` dentro da mesma empresa
feita em Python (mesma semântica da collation real `utf8mb4_0900_ai_ci`),
antes de qualquer INSERT/UPDATE - o erro de integridade do banco nunca
chega a estourar cru na tela; a `UniqueConstraint` continua como rede de
segurança.

### Fase 3 - Atualização dos pontos de leitura mapeados na Fase 0

Regra aplicada consistentemente: **contexto paciente/atendente** (painel de
TV, tela de Atendimento) usa `nome_exibicao`; **contexto administrativo**
(listagens, Iniciar Turno, rodízio, ocupação, mensagens flash de
admin/turno) usa `identificador_admin`.

- `nome_exibicao`: `_nome_local()`/`_payload_chamada()`/
  `historico_chamadas_tv()` (painel de TV, ao vivo e histórico),
  `_serializar_atendida()`/`_serializar_senha_atual()` (JSON de
  atendidas/senha atual), cabeçalho "Local" e tabela de atendidas em
  `painel_fila.html`/`painel_local.html`, flash "Senha X chamada para Y".
- `identificador_admin`: dropdown "Local" em Iniciar Turno (não remonta
  mais o prefixo no template - `identificador_admin` já vem pronto),
  `escolher_local.html` (tela legada equivalente), flash "local já em
  uso"/"Turno iniciado em"/"Local de atendimento" (`turno.py` e
  `atendimento/local.py` - mesma tela/fluxo, coerência confirmada pelo
  cliente), listagem admin de Locais (coluna primária, com
  `nome_exibicao` como coluna extra de referência - não substitui),
  formulário admin, tela de ocupação, título de "Configurar rodízio".
  Ordenação (`order_by`) de todas as queries que alimentam essas telas
  (`locais_acessiveis()`, `usuarios_ativos_por_local/da_empresa()`,
  `admin/locais.py::listar_locais`, `admin/helpers.py::locais_da_empresa` -
  esta última sem chamador hoje, mantida por consistência) passou a
  acompanhar o mesmo campo exibido em cada tela.
- **`identificador_admin` puro** (`relatorios/helpers.py::mapas_nomes()`,
  consumido por `core/routes.py::_ultimos_atendimentos_enriquecidos` e
  `relatorios/routes.py` - `dashboard`, `tempo_atendimento_view`): a
  primeira versão combinava `"[fila] · [identificador_admin]"` na mesma
  célula (motivo original: essas telas podem ser vistas por
  atendente/executor também, não só admin/gerencial). **Revertido** depois
  de conferência visual real (screenshot pedido explicitamente pelo
  cliente antes de fechar o adendo) - as três tabelas (`core/dashboard.html`,
  `relatorios/dashboard.html`, `relatorios/tempo_atendimento.html`) já têm
  uma coluna "Fila" própria ao lado, então o formato combinado repetia o
  nome da fila e quebrava em 2 linhas na maioria das linhas da tabela.
  Voltou a mostrar só `identificador_admin` na coluna "Local", sem repetir
  a fila (que já está na coluna vizinha).
- **Cupom impresso do totem:** removido do escopo - engano do próprio
  cliente na formulação do pedido original (queria dizer nome da FILA, não
  `Local.nome`); confirmado que o cupom não referencia `Local` hoje
  (`Senha.local_id` ainda é `None` no momento da impressão).

**Testes:** os 29 arquivos com `Local(...)` sem os campos novos foram
corrigidos - cada um recebeu `nome_exibicao`/`identificador_admin`
computados com a MESMA convenção da produção (`identificador_admin`
prefixado por `[SIGLA]` quando o teste dependia de multi-unidade/telas
administrativas), não valores arbitrários - confirmado caso a caso contra
assertions existentes (ex.: `test_regressao_contexto_multi_unidade.py` já
esperava `"[MAT] Guiche 01"`/`"[ANE] Guiche 01"` no dropdown de Iniciar
Turno, o que validou a convenção). Dois arquivos
(`test_regressao_fase10_3.py`, `test_regressao_local_filas_destino.py`)
tinham POSTs diretos ao formulário admin usando o campo antigo `nome` -
atualizados para `nome_exibicao`/`identificador_admin`, com a mensagem de
erro esperada também atualizada ("Informe o nome de exibição do local").
Suíte completa (40 arquivos) sem falhas.

**Arquivos alterados:** `app/models/local.py`,
`migrations/versions/5ba0cdce86cb_*.py`, `app/admin/locais.py`,
`app/admin/helpers.py`, `app/templates/admin/locais/{form,listar,ocupacao,
rodizio}.html`, `app/atendimento/{helpers,chamada,turno,local}.py`,
`app/templates/atendimento/{painel_fila,painel_local,iniciar_turno,
escolher_local}.html`, `app/relatorios/helpers.py`, `app/relatorios/
routes.py`, `app/core/routes.py`, 29 arquivos de teste.

**Pendente (fase futura, não nesta sessão):** remover a coluna `nome`
(legado) de `Local` - só depois de uma janela de confiança em produção com
os campos novos, seguindo o mesmo princípio já usado para
`app/totem/impressao.py` (10.28) de não remover código/dado no mesmo
movimento em que a substituição é introduzida.

---

## 10.33. ADENDO — Fallback de áudio pré-gravado no painel de TV (Web Speech API quebrada em Smart TV)

> **Causa raiz** (confirmada empiricamente, não presumida): no navegador
> embarcado de uma Smart TV Samsung física, a Web Speech API
> (`speechSynthesis`) **existe e "finge funcionar"** - aceita
> `speechSynthesis.speak()`, dispara o evento `onend` sem erro - mas **nenhum
> som sai**. O mesmo painel fala normalmente em navegador de computador
> (Chrome/Firefox). Ou seja, o motor de TTS do sistema operacional da TV é
> não-funcional apesar da API estar presente; não é bug de código nem de
> escolha de voz (a correção de voz do adendo 10.13 não tem como ajudar).

**Teste de isolamento que fechou o diagnóstico** (antes de decidir a solução):
adicionado temporariamente um botão que toca um `.wav` estático via `<audio>`/
`new Audio().play()` comum, **sem tocar em `speechSynthesis`**. Na TV real, o
beep tocou perfeitamente (confirmado com cliques reais). Conclusão: a saída de
áudio do navegador funciona; só a Web Speech API está quebrada. Portanto a
solução é o fallback de áudio pré-gravado já previsto na **seção 6** do
BLUEPRINT ("fallback para arquivos pré-gravados se a Smart TV não suportar TTS
bem em PT-BR").

**1. Configuração por TV, não global** (`Tv.usa_audio_pregravado`, boolean,
default `False` - migration `f01b63e1bf65`, `server_default='0'`): só as TVs
com o navegador quebrado ligam o fallback; TVs normais continuam no
`speechSynthesis` **sem nenhuma regressão** (o caminho antigo é intocado, só
ganhou um desvio no topo de `falarChamada()`). Exposto no cadastro admin de TV
com um aviso explicando quando marcar.

**2. Áudio montado por concatenação de clipes gravados** (`app/static/audio/`,
46 arquivos `.wav` versionados no repo, PCM 44100 Hz/16-bit/mono):
- **Frases fixas:** `frase_atencao_senha` ("Atenção, senha"), `frase_dirija_se`
  ("Por favor, dirija-se a").
- **26 letras** (`letra_a`…`letra_z`, alfabeto completo - as 16 usadas hoje
  + 10 de seguro para siglas futuras) e **10 números** (`numero_0`…`numero_9`,
  dígito a dígito - "003" vira "zero zero três").
- **Locais:** um arquivo por nome de exibição distinto, `local_<slug>.wav`.
- Ordem da chamada de senha: `frase_atencao_senha` → letras/números do código
  (um clipe por caractere) → `frase_dirija_se` → `local_<slug>`. Cada `<audio>`
  toca no `ended` do anterior, sem sobreposição
  (`app/templates/painel/tv.html::falarChamadaPregravada`).

> **⚠️ COMPORTAMENTO ESPERADO, NÃO BUG — chamada por nome no modo
> pré-gravado NÃO fala o nome do paciente.** Na 2ª etapa da coleta (chamada
> por nome), o áudio pré-gravado anuncia **apenas** "Por favor, dirija-se a
> [local]" (`frase_dirija_se` → local), **sem o nome do paciente**. Motivo
> técnico: o nome do paciente é texto livre arbitrário digitado no totem,
> impossível de pré-gravar clipe por clipe (diferente do modo
> `speechSynthesis`, que sintetiza qualquer texto). Isto é a **opção (i)**
> decidida com o cliente ANTES da implementação, **testada ao vivo na TV
> real e mantida como está por decisão explícita do cliente**. O paciente se
> identifica vendo o nome na TELA (que mostra o nome normalmente); só o
> ÁUDIO omite o nome. **Não tratar como defeito numa sessão futura.** (No
> modo `speechSynthesis` normal, o nome continua sendo falado como sempre -
> essa omissão é exclusiva do fallback pré-gravado.)

**Opções de "humanização" avaliadas e conscientemente DESCARTADAS na Fase 0
(decisão de não evoluir, não pendência esquecida):** a fala pré-gravada é
deliberadamente mais "seca"/robótica que uma síntese de voz - duas melhorias
de naturalidade foram consideradas e recusadas de propósito, cada uma com seu
motivo:
- **Números por extenso** ("senha vinte e três" em vez de "dois três"):
  descartado por exigir ~39 clipes + uma lógica de concatenação frágil
  (`cem` vs `cento`, o conector "e" nos lugares certos, centenas/dezenas)
  contra os **10 clipes** (`numero_0`…`numero_9`) e a concatenação trivial do
  dígito-a-dígito. O código lê "003" como "zero zero três" - menos natural,
  porém à prova de erro e cobrindo qualquer número (001–999) com 10 arquivos.
- **Preposição com concordância de gênero** ("dirija-se **ao** guichê" /
  "dirija-se **à** coleta"): descartado para não ter que gravar as duas
  variações nem embutir lógica de gênero por local. Mantido o invariável
  "Por favor, dirija-se a [local]", que **já era** o texto do template de
  chamada da empresa antes do fallback - consistente com o que a TV já falava
  no modo `speechSynthesis`.

Se um dia se quiser evoluir a naturalidade, o caminho está registrado aqui -
mas é uma escolha de produto (mais clipes/lógica em troca de fala mais
natural), não um bug a corrigir.

**3. Slug canônico ÚNICO, no servidor** (`app/utils/slug.py::
gerar_slug_audio_local`): separador **underscore** (`local_guiche_01.wav`),
diferente de `gerar_slug_base` que usa hífen para URL. O slug é calculado só no
Python e enviado PRONTO no payload da chamada (`local_audio_slug`, em
`_payload_chamada`) - **o JS do painel nunca recalcula slug nenhum**. Decisão
deliberada: eliminar a divergência entre "o que o cadastro salva" e "o que a TV
busca no disco" **por construção** (uma implementação só), em vez de manter duas
e tentar detectá-la por teste. Uma tabela dourada em Python
(`tests/test_regressao_audio_pregravado.py`) trava o comportamento exato do
algoritmo (acento, caixa, hífen, espaço, ordinal, vazio).

**4. Rede de segurança para Local sem áudio gravado** (nunca fica mudo):
- `local_audio_slug` só viaja no payload **quando o arquivo
  `local_<slug>.wav` existe no disco** (checado no servidor, `os.path.exists`);
  senão vem `None`. Com `None`, o painel toca `local_generico.wav` ("Por favor,
  dirija-se ao local indicado no painel") **no lugar do par** `frase_dirija_se`
  + local (não anexado depois - o genérico já é a frase completa, sem a
  redundância "dirija-se a … dirija-se ao"). Rede adicional no cliente: se o
  arquivo do local existir no disco mas falhar ao carregar na TV (`onerror`),
  cai no genérico também.
- **Aviso não-bloqueante no cadastro de Local** (Opção A): ao salvar um Local,
  se `local_<slug>.wav` não existe **E** há ≥1 TV na MESMA unidade com
  `usa_audio_pregravado` ligado, um flash avisa que aquele local será anunciado
  com a frase genérica até o arquivo ser adicionado. Sem TV com o fallback
  naquela unidade, não avisa (evita ruído/falso positivo).

**Não-duplicação preservada (adendo 10.13):** o disparo de áudio continua
saindo só do evento `chamada` ao vivo (`socket.on("chamada")`), nunca de
`recarregarHistorico()` (reload/reconexão) - vale para os dois caminhos, já que
ambos passam pelo mesmo `falarChamada()`. O botão "Tocar" (repetir) continua
funcionando igual (reusa o mesmo evento `chamada` com `repetir:true`).

**Processo para cadastrar um Local NOVO numa unidade com TV pré-gravada:**
(a) cadastrar o Local normalmente; (b) gerar/gravar o áudio da pronúncia do
`nome_exibicao`; (c) nomear `local_<slug>.wav` (o slug é o mesmo mostrado no
aviso do cadastro) e colocar em `app/static/audio/`; (d) até isso, a TV usa o
genérico automaticamente - nunca fica muda.

**Áudio gravado:** os 46 clipes foram gravados manualmente pelo cliente (mesma
voz), entregues a 24000 Hz e **reamostrados para 44100 Hz** (numpy, interpolação
linear - transparente para voz já limitada a 12 kHz) por 44100 ser a única taxa
comprovadamente tocável nesta TV (a do beep do teste de isolamento). Originais
24000 preservados em backup.

**Verificação FINAL na TV real (concluída):** o fallback foi confirmado
funcionando de ponta a ponta na Smart TV física - a frase concatenada tocou
**correta e automaticamente, sem gesto do usuário** (a preocupação com autoplay
de `<audio>` sem interação NÃO se concretizou nesta TV). A chamada por nome no
modo pré-gravado também foi testada ao vivo e confirmada tocando só o local (sem
o nome), como esperado. Diagnóstico e desenvolvimento também cobertos por:
tabela dourada do slug + payload (com/sem arquivo) + render TV com/sem flag +
genérico + não-duplicação + aviso de cadastro
(`tests/test_regressao_audio_pregravado.py`, 13 casos); os 7 clipes da sequência
"EX003 → Guiche 01" decodificam sem erro num engine real (Chrome headless).

**Instrumentação de debug removida após a confirmação:** os vestígios de debug
usados no diagnóstico foram apagados depois do fallback confirmado na TV - o
botão/overlay `?debug_audio_html5=1` (teste de isolamento do beep), o overlay
`?debug_voz=1` (log de voz/sequência) e o arquivo `app/static/audio/debug_beep.wav`.
O painel voltou ao estado limpo; render revalidado sem erro de JS. Suíte completa
(41 arquivos) sem falhas após a limpeza.

**Arquivos alterados:** `app/models/tv.py` (+ migration `f01b63e1bf65`),
`app/utils/slug.py`, `app/atendimento/helpers.py` (`_payload_chamada` +
`caminho_audio_local`/`_slug_audio_local_se_existe`), `app/admin/tvs.py` +
`app/templates/admin/tvs/form.html` (campo), `app/admin/locais.py` (aviso),
`app/templates/painel/tv.html` (concatenação + desvio em `falarChamada`),
`app/static/audio/` (46 `.wav`). Testes: novo
`test_regressao_audio_pregravado.py`; 4 testes de payload ajustados para
ignorar `local_audio_slug` no match (helper `_sem_slug_audio`).

---

## 10.34. ADENDO — Remoção da etapa "Quantidade de acompanhantes" no totem (supersede parte da 10.5)

> Pedido do cliente: a etapa "Quantidade de acompanhantes" (tela extra no
> totem antes de emitir a senha, introduzida na 10.5) foi considerada
> desnecessária no fluxo real e removida - o paciente toca o serviço e a
> senha sai direto, sem uma pergunta intermediária que ninguém usava.

**1. Fluxo direto (sem tela de confirmação extra):** as folhas do menu do
totem (`totem/menu.html`) que geram senha agora se comportam assim
(`_decorar_botoes` calcula `precisa_escolher_tipo`/`tipo_unico` por botão):
- **0 ou 1 tipo de senha ativo na fila** → o botão é um `<form method="post">`
  que **POSTa direto** em `totem.gerar_senha` (com o `tipo_senha_id` embutido
  como hidden quando há exatamente 1 tipo). Nenhuma tela intermediária.
- **2+ tipos ativos** → ainda precisa de uma escolha real (não dá pra pular),
  então vai para a tela de escolha de tipo. O mesmo vale para
  `totem/escolher_tipo.html`, cujos botões de tipo também viraram POST direto
  em `gerar_senha`.

**2. `confirmar_acompanhantes.html` → `confirmar_geracao.html`:** o template
da tela de acompanhantes foi **renomeado/repurposado** (git detecta como
rename ~60%) - deixou de coletar quantidade de acompanhantes e passou a ser
só a confirmação/escolha de tipo quando ela é necessária (rota
`totem.confirmar_geracao`, mantida como caminho para o caso de 2+ tipos e
como fallback defensivo).

**3. Campo/coluna preservados, só a UI saiu** (mesmo princípio de "desativar,
não apagar" já usado na 10.28/10.32): `Senha.qtd_acompanhantes` (coluna) e
`QTD_ACOMPANHANTES_MAXIMO` continuam existindo; `gerar_senha()` ainda aceita
`qtd_acompanhantes` do form (default 0 quando o POST direto não manda nada).
Nada quebra e a coleta pode ser reativada no futuro sem migration - só a
etapa de UI que pedia o número foi removida.

**Arquivos alterados:** `app/templates/totem/menu.html`,
`app/templates/totem/escolher_tipo.html`,
`app/templates/totem/confirmar_acompanhantes.html` → `confirmar_geracao.html`
(rename), `app/totem/routes.py` (`_decorar_botoes`, rotas
`confirmar_geracao`/`gerar_senha`). **Cross-ref:** supersede a parte "Quantidade
de acompanhantes" da **10.5** (o resto da 10.5 - navegação multi-nível do
totem - continua valendo).

---

## 10.35. ADENDO — Super admin com senha aleatória + troca obrigatória no 1º login (hardening para publicação no GitHub)

> Contexto: ao preparar o repositório para publicação no GitHub, a senha fixa
> hardcoded do super admin inicial (`"ChangeMe123!"`) era um risco óbvio - um
> segredo previsível versionado no código. Trocada por um fluxo seguro por
> construção.

**1. Senha inicial aleatória** (`seeds/seed_inicial.py`): o super admin
(`login="superadmin"`) é criado com `secrets.token_urlsafe(12)` - nunca uma
senha fixa versionada. A senha temporária é **impressa no stdout do seed**
(uma vez, para o operador do primeiro deploy anotar), nunca gravada em
arquivo/repo.

**2. Troca obrigatória antes de liberar qualquer tela**
(`Usuario.deve_trocar_senha`, boolean default `False`; middleware
`app/middleware/forcar_troca_senha.py`, registrado em `app/__init__.py`): o
super admin nasce com `deve_trocar_senha=True`. Um `before_request` global
redireciona **toda** requisição autenticada para `auth.trocar_senha_obrigatoria`
enquanto a flag estiver ligada - exceto os endpoints estritamente necessários
(`ENDPOINTS_PERMITIDOS = {trocar_senha_obrigatoria, logout, static}`). Rotas
públicas (totem/painel de TV, sem login) nunca são afetadas
(`current_user.is_authenticated` é o primeiro gate). Ao definir a senha nova,
`auth/routes.py` zera a flag. Resultado: a senha temporária impressa pelo seed
**não tem como virar credencial permanente** - o sistema bloqueia tudo até a
troca.

**Arquivos:** `seeds/seed_inicial.py`, `app/models/usuario.py` (coluna
`deve_trocar_senha`), `app/middleware/forcar_troca_senha.py`,
`app/__init__.py` (registro), `app/auth/routes.py` +
`app/templates/auth/trocar_senha_obrigatoria.html`. **Testes:**
`tests/test_regressao_forcar_troca_senha.py` cobre: seed gera senha aleatória
(nunca mais `"ChangeMe123!"` - asserção negativa explícita), a flag bloqueia
telas operacionais/admin e libera só a troca/logout, e a troca zera a flag.
*(Este trabalho já estava no commit inicial do repositório; o adendo o
documenta retroativamente, que faltava.)*

---

## 10.36. ADENDO — Integração contínua (GitHub Actions) + compat Linux do `win32print`

> Contexto: com o repositório no GitHub, faltava rodar a suíte
> automaticamente a cada push (nada garantia que um commit não quebrou os
> testes), e a suíte nem sequer **importava** num runner Linux por causa de um
> `import win32print` incondicional.

**1. Workflow de CI** (`.github/workflows/tests.yml`): roda em `push` e
`pull_request` (qualquer branch - projeto de um contribuidor só, suíte
rápida). `ubuntu-latest`, Python 3.11, `pip install -r requirements.txt`,
e roda **cada** `tests/test_regressao_*.py` como script standalone
(`python -m tests.<modulo>`, mesmo padrão local - não é pytest), agrupando a
saída por módulo e falhando o job se qualquer um falhar. **Sem MySQL/service
container:** a suíte sempre usa `create_app("testing")` → `TestingConfig`
(SQLite em memória) - não é limitação do CI, é como a suíte já roda no dia a
dia.

**2. `win32print` tolerante a ausência** (`app/totem/impressao.py`): o import
virou `try: import win32print / except ImportError: win32print = None` no
nível do módulo. `pywin32` só instala em Windows (`requirements.txt`,
`sys_platform == "win32"`), mas `impressao.py` é importado por
`totem/routes.py` → `create_app()`, ou seja, **toda** a aplicação (suíte
inteira + o deploy real em Linux, seção 10) passa por ele - um import
incondicional quebraria tudo em qualquer ambiente não-Windows. Mantido como
**atributo de módulo** (não import local dentro da função) de propósito: os
testes do mecanismo legado (`test_regressao_fase10_6.py`) fazem
`patch.object(impressao, "win32print")`, que exige o nome no módulo. O único
uso real (`enviar_para_impressora_windows`) só roda quando há impressora
configurada - cenário que já pressupõe Windows - e está dentro do tratamento
de falha não-fatal que a 10.6/10.28 já garantia.

**Arquivos:** `.github/workflows/tests.yml` (novo), `app/totem/impressao.py`.

**3. A PRIMEIRA execução real do CI já pegou um bug de teste que a suíte
local escondia (exatamente o motivo de ter CI):** o CI rodou às ~23:16 UTC e
**dezenas de testes falharam**, embora passassem localmente. Causa raiz: o
middleware de encerramento automático (10.18) usa `datetime.now()` (hora
local do servidor) e, passado o `HORARIO_ENCERRAMENTO_SESSOES` (default
"23:00"), cancela toda senha `chamada`/`em_atendimento` e derruba a sessão
de atendimento a **cada** request. Rodando depois das 23h (o runner é UTC),
ele quebrava todo fluxo autenticado. A suíte **não era hermética** quanto ao
relógio - passava só porque era rodada antes das 23h no fuso local.
**Correção:** novo gate `ENCERRAMENTO_SESSOES_ATIVO` (`app/config.py`) -
`True` em prod/dev, `False` em `TestingConfig`; o middleware
(`app/middleware/encerramento.py`) retorna cedo quando desligado. O teste
dedicado (`test_regressao_encerramento_automatico`) **religa** o gate no
ponto exato em que quer exercitar o encerramento. Provado reproduzindo a
falha localmente com `HORARIO_ENCERRAMENTO_SESSOES=00:00` (toda a suíte quebra
sem o fix, toda passa com o fix). **Arquivos:** `app/config.py`,
`app/middleware/encerramento.py`, `tests/test_regressao_encerramento_automatico.py`.

> **⚠️ Observação de PRODUÇÃO revelada por este bug → RESOLVIDA na 10.37:** o
> middleware comparava `datetime.now()` (hora local do SO). Num servidor UTC,
> o encerramento das 23:00 dispararia às 20:00 de Brasília. Em vez de depender
> de config de fuso do SO (frágil), o cálculo passou a resolver o fuso da
> clínica **no código** - ver **10.37**.

> **Nota menor (mesma rodada de housekeeping):** os ícones da navegação
> admin (`app/templates/admin/_layout.html`) foram diferenciados - 🏢 Empresas,
> 🛡 Perfis, 🏥 Unidades - que antes usavam ícones repetidos/genéricos,
> dificultando a distinção rápida no menu. Mudança puramente cosmética, sem
> impacto de comportamento.

---

## 10.37. ADENDO — Fuso da clínica resolvido no código (não no SO do servidor)

> Contexto: a observação da 10.36 (encerramento automático poderia disparar 3h
> cedo num servidor UTC) é **risco real de comportamento errado em produção**,
> não dívida cosmética - resolver via configuração de fuso do SO
> (`TZ=America/Sao_Paulo`) seria frágil: um redeploy ou reconfiguração de
> infraestrutura pode resetar isso silenciosamente, sem ninguém perceber, e o
> encerramento (e o horário das filas) passaria a disparar na hora errada sem
> nenhum erro visível. Por isso o fuso é resolvido **no código**.

**1. Descoberta - o bug estava em DOIS lugares, não um** (ambos usavam
`datetime.now()`, naive, fuso do SO):
- `app/middleware/encerramento.py` - o encerramento automático das 23h (10.18).
- `app/models/fila.py::Fila.aberta_em()` - o horário de funcionamento das filas
  (10.1), que inclusive **documentava** a suposição ("assume que o servidor está
  no fuso da clínica"). Num servidor UTC, filas apareceriam abertas/fechadas 3h
  fora. O deploy real (`deploy/painel-senhas.service`) **não seta `TZ`**, então
  o app herdaria o fuso do SO - risco não-mitigado até aqui.

**2. Util único de hora de parede** (`app/utils/tempo.py`):
`agora_na_clinica()` = instante atual convertido para o fuso da clínica
(config `FUSO_HORARIO`, default `America/Sao_Paulo` - `app/config.py`), via
`datetime.now(ZoneInfo(...))`, que **expressa o instante corrente no fuso pedido
independentemente de como o SO está configurado**. `_instante_utc()` é um seam
separado só para os testes simularem "o relógio do servidor está em tal
instante UTC". Os dois call-sites (encerramento e `aberta_em`) passaram a usar
esse util. `tzdata` adicionado ao `requirements.txt` para o `zoneinfo` funcionar
também em Windows/containers mínimos que não trazem a base IANA do SO.

**3. Teste que trava o cenário real de risco**
(`tests/test_regressao_fuso_horario.py`): cada caso **simula o relógio do
servidor em UTC** (patch de `_instante_utc`) e confirma a decisão no horário de
Brasília - com o código antigo (`datetime.now()` naive num servidor UTC) daria
o resultado ERRADO, é essa diferença que o teste prende:
- `agora_na_clinica()`: 02:30 UTC → 23:30 Brasília (UTC-3).
- `Fila.aberta_em()` (aberta só à tarde): 23:30 Brasília = ABERTA / 10:30
  Brasília = FECHADA.
- Encerramento end-to-end com o horário PADRÃO "23:00": **dispara** às 23:30
  Brasília (servidor 02:30 UTC) e **não** dispara às 22:30 Brasília (01:30 UTC).
  O código antigo leria 02:30 < 23:00 e não encerraria - os 3h de erro que o
  teste elimina.

**Arquivos:** `app/utils/tempo.py` (novo), `app/config.py` (`FUSO_HORARIO`),
`app/middleware/encerramento.py`, `app/models/fila.py`, `requirements.txt`
(`tzdata`), `.env.example` (documenta `FUSO_HORARIO`); novo
`tests/test_regressao_fuso_horario.py`. **Deploy:** o `TZ` do SO deixou de ser
requisito de correção - se a clínica não estiver no horário de Brasília, basta
`FUSO_HORARIO=<outro fuso IANA>` no `.env`.

---

## 10.38. ADENDO — Correção do layout do cupom impresso (centralização + contraste) e procedimento de impressão

> Reportado a partir de impressão FÍSICA real (não só a prévia em tela): o cupom
> (10.28, Fase 2) saía **alinhado à esquerda, ocupando menos da metade da largura
> do rolo**, e com **contraste desigual** entre linhas (algumas nítidas, outras
> muito claras).

**1. Causa raiz da centralização** (`@media print` de `totem/senha_gerada.html`)
- exigiu DUAS rodadas, com um sub-bug de CSS sutil no meio:
- **1ª tentativa (insuficiente):** trocar `position: absolute; width: 100%` por
  `left: 0; right: 0`. **Não resolveu na impressão física** - reportado pelo
  cliente e depois reproduzido aqui.
- **Sub-bug descoberto (a causa real):** a regra base `.cupom` tem `width: 320px`
  (só pra prévia na tela). A regra `@media print` definia `left:0; right:0` mas
  **não resetava o `width`**. Pela spec do CSS, num elemento absoluto com `left`,
  `right` E `width` todos definidos, o **`right` é IGNORADO** e o elemento fica
  com `width: 320px` em `left: 0` → bloco de 320px colado na esquerda, faixa
  branca à direita. **Correção final:** adicionar **`width: auto`** na regra de
  impressão (com `left:0; right:0; width:auto` o cupom estica de borda a borda da
  página = largura do papel), com `text-align:center` e `color:#000` explícitos.
- **Confirmado por render de impressão REAL** (`--print-to-pdf` do Chrome, mesmo
  motor do físico, renderizado com PyMuPDF): sem `width:auto` o cupom sai a 320px
  na esquerda (reproduz o físico); com `width:auto` ocupa a largura inteira,
  centralizado. Isso também **provou que a causa era o CSS, não driver/escala/
  diálogo** - o print-to-pdf não passa por nenhum deles e reproduziu o mesmo
  desalinhamento. (Lição: a simulação anterior "print na tela" falhou por eu ter
  adicionado `width:auto` só nela, mascarando o bug - só o render de impressão de
  verdade pegou.)
- **3ª rodada - o `width:auto` centralizou no PDF (Letter) mas o físico CONTINUOU
  à esquerda.** Investigação: (i) confirmado que `--print-to-pdf` **ignora
  `@page { size }`** e sempre usa Letter 216mm - rendi 3 variantes (`auto`,
  `80mm`, `58mm`) e todas deram 216mm, então o PDF nunca testou a largura do
  rolo; (ii) descartado ancestral posicionado/estreito (o `.totem`/`body` são
  estáticos, então o bloco contêiner do `position:absolute` já é a página).
  **Causa raiz final:** com `@page { size: auto }`, quem define a largura da
  página é o driver; se o papel configurado no driver é MENOR que o rolo físico,
  o cupom centraliza numa página estreita e imprime encostado à esquerda do rolo
  mais largo → faixa branca à direita. **Correção:** `@page { size: 80mm auto }`
  explícito (largura padrão de rolo térmico de totem; técnica padrão de cupom
  térmico via navegador) - a página vira determinística = largura do rolo, e o
  `left:0/right:0/width:auto/text-align:center` centraliza de fato no papel. **Se
  o rolo não for 80mm, trocar o valor** no CSS e garantir que o tamanho de papel
  do driver Windows bata. **Validação:** só na impressora física - o print-to-pdf
  não serve para `@page size` (pendente de confirmação do cliente no físico).

**2. Causa raiz do contraste** (impressora térmica é 1-bit - ponto preto ou nada):
só duas linhas eram bold (`.cupom-empresa` 700, `.cupom-codigo` 800); o resto
(`.cupom-linha`: fila, endereço, telefone, data/hora) era **peso normal (400)** e
o separador era **cinza `#95A5A6`** - traços finos e cinza saem claros/pontilhados.
**Correção:** peso **uniforme bold (700)** em todas as linhas (`.cupom p`), com a
hierarquia vindo **só do tamanho**, nunca do peso, e separador preto. Estrutura
reordenada pra reforçar a hierarquia: empresa (médio) → fila (menor, MAIÚSCULO)
→ **código (maior, negrito)** → data/hora + endereço/telefone (rodapé, menor).

> **Nota posterior (sem adendo próprio - ajuste de layout, não correção de
> bug):** as duas linhas tracejadas separadoras (`.cupom-separador`, texto
> literal `- - - - ...`, nunca foi um `border` CSS) foram **removidas** a
> pedido do cliente, por um layout mais limpo (referência: cupom de senha da
> Caixa Econômica, sem separador). O respiro visual entre os blocos passou a
> vir de `margin-top` em `.cupom-fila`/`.cupom-datahora`, não de uma linha.
> Estrutura atual: empresa → fila → código → data/hora → endereço/telefone,
> sem nenhum separador entre eles.

**3. Avaliação: dá pra forçar as configs do diálogo de impressão via CSS?**
(pedido explícito - a alternativa a depender de alguém marcar as opções certas
toda vez):
- **Margens: SIM (via CSS).** `@page { margin: 0 }` já torna a margem padrão 0 -
  o diálogo usa isso por padrão; só uma escolha manual de "margem personalizada"
  reintroduz margem.
- **Escala (100% vs "Ajustar à página"): NÃO há CSS que force isso.** Porém, com
  a correção do item 1, o layout ficou **tolerante a escala**: o cupom ocupa a
  largura inteira e centraliza independente da escala, então "Ajustar à página"
  não quebra mais o alinhamento (no máximo muda o tamanho geral, o layout
  continua certo).
- **`@page { size }`** foi mantido em `auto` (decisão da 10.28: larguras de rolo
  incertas entre os totens). Fixar `size: <largura> auto` reduziria ainda mais a
  dependência do diálogo/driver, mas cravaria uma largura de papel - só vale se a
  largura real for padronizada/confirmada. Fica registrado como opção futura.

**4. Solução robusta pra ZERO dependência de diálogo (recomendação de deploy do
totem):** rodar o navegador do totem em **modo kiosk-printing** (Chrome/Edge com
`--kiosk-printing`): `window.print()` imprime **direto na impressora padrão, sem
mostrar o diálogo**, usando as configs salvas. Elimina a escolha manual a cada
impressão - é a forma de não depender de ninguém lembrar das opções nem do
Windows resetar o padrão numa atualização.

> **⚙️ PROCEDIMENTO DE IMPRESSÃO DO TOTEM (passo obrigatório de deploy - para não
> virar mistério futuro "por que às vezes o cupom sai errado"):**
> 1. **Preferencial:** iniciar o navegador do totem com `--kiosk-printing` (ou o
>    equivalente do navegador embarcado), com a impressora térmica como padrão do
>    Windows. Sem diálogo, impressão imediata e consistente.
> 2. **Se NÃO usar kiosk-printing** (diálogo aparece a cada impressão), as opções
>    obrigatórias no diálogo são: **Impressora = a térmica**, **Margens = Nenhuma**
>    (ou Padrão, já que o CSS zera com `@page margin:0`), **Escala = 100%** (NUNCA
>    "Ajustar à página"). Windows pode resetar isso numa atualização - conferir
>    após updates.

**5. Teste:** o **contraste** foi confirmado na impressora térmica física real
pelo cliente (todas as linhas nítidas e uniformes). A **centralização** foi
validada por renderização de impressão real via `--print-to-pdf` do Chrome
(motor de impressão de verdade), renderizada em imagem com PyMuPDF - o cupom
passou a ocupar a largura inteira, centralizado. A confirmação final na térmica
física (com o procedimento de impressão acima) fica com o operador. Teste de
regressão `tests/test_regressao_cupom_impressao_navegador.py` ganhou 2 casos:
centralização (exige `left:0`/`right:0` **+ `width:auto`** + `text-align:center`,
e proíbe `width:100%` de voltar) e peso bold uniforme (sem `.cupom-linha` normal
nem separador cinza). Suíte completa (42 arquivos) sem falhas.

**Arquivos alterados:** `app/templates/totem/senha_gerada.html`,
`tests/test_regressao_cupom_impressao_navegador.py`.

---

## 10.39. ADENDO — Reset diário do sequencial usava dia civil em UTC, não da clínica (3º call-site do bug da 10.37)

> Reportado durante a escrita de testes de regressão para o reset diário do
> sequencial (seção 8: "Reset de sequencial à meia-noite"): ao simular o
> relógio do servidor em UTC (mesmo padrão de `test_regressao_fuso_horario.py`,
> 10.37) para exercitar a virada de dia, o sequencial resetava 3 horas ANTES
> da meia-noite real de Brasília.

**1. Causa raiz** (`app/totem/routes.py::_emitir_senha`): a query que decide
se uma senha antiga é "de hoje" (pra saber se o sequencial deve reiniciar,
`Fila.zera_diariamente`) comparava:
```python
func.date(Senha.criada_em) == datetime.utcnow().date()
```
Isso **parecia** correto por comparar UTC com UTC (`criada_em` é gravado em
UTC naive, `datetime.utcnow()` também é UTC) - mas o dia CIVIL que importa
pro negócio é o da CLÍNICA, não o de UTC. A virada de dia em UTC acontece às
**21h em Brasília** (UTC-3), 3h antes da meia-noite real. Resultado: toda
noite, entre 21h e 23:59 (Brasília), a primeira senha gerada depois desse
horário resetava o sequencial pra 001 - **colidindo com um código já
impresso/chamado mais cedo no mesmo dia civil**. É a mesma classe de bug que
a 10.37 corrigiu em `app/middleware/encerramento.py` e
`app/models/fila.py::aberta_em()` - só que este terceiro call-site escapou
daquela correção porque a comparação "UTC com UTC" mascarava o problema (não
tinha o cheiro óbvio de `datetime.now()` naive que os outros dois tinham).

**2. Segundo call-site com o MESMO padrão, encontrado ao investigar:**
`app/atendimento/helpers.py::historico_chamadas_tv` (10.13, item 2 - histórico
"de hoje" do painel de TV) já documentava explicitamente "mesma janela usada
no reset diário do sequencial (totem/routes.py)" - ou seja, replicava o
mesmo bug por construção deliberada de manter os dois consistentes entre si.
Corrigir só um dos dois teria consertado um e deixado o painel de TV mostrando
"hoje" com um recorte diferente do sequencial - por isso os dois foram
corrigidos juntos, reaproveitando a mesma função.

**3. Correção — janela de datas em UTC, não `func.date()`:** nova função
`janela_dia_clinica_em_utc(momento_clinica=None)` (`app/utils/tempo.py`, ao
lado de `agora_na_clinica()`): calcula a meia-noite de "hoje" e "amanhã" no
fuso da clínica e converte os dois limites pra instantes UTC naive, devolvendo
`(inicio_utc, fim_utc)`. Os dois call-sites passaram a filtrar por **range**
(`Senha.criada_em >= inicio_utc, Senha.criada_em < fim_utc` /
`Senha.chamada_em >= inicio_utc, Senha.chamada_em < fim_utc`), nunca mais por
igualdade de `.date()` contra `datetime.utcnow().date()`. Comparar por janela
em vez de igualdade de data evita reintroduzir esta classe de bug numa quarta
função: qualquer novo código que precisar filtrar "isso é de hoje na clínica"
sobre uma coluna UTC deve reaproveitar `janela_dia_clinica_em_utc()`, nunca
comparar `.date()` contra `datetime.utcnow()`/`agora_na_clinica()` diretamente.

**4. Teste que trava o cenário** (`tests/test_regressao_sequencial_diario.py`,
novo arquivo): simula o relógio do servidor em UTC (mesmo padrão da 10.37) e
confirma que o sequencial NÃO reseta na faixa 21h-23:59 Brasília (checkpoints
em 20:00, 21:00, 22:00, 23:00 e 23:59 do mesmo dia civil - com o código antigo,
o primeiro checkpoint depois das 21h já resetava, o que o teste pega) e reseta
de verdade só à meia-noite real de Brasília. `tests/test_regressao_tv_historico.py`
(pré-existente, cobre `historico_chamadas_tv`) segue passando sem alteração,
confirmando que a troca de implementação não mudou o comportamento observável
do painel de TV, só corrigiu o limite exato da janela.

**Arquivos alterados:** `app/utils/tempo.py` (nova
`janela_dia_clinica_em_utc`), `app/totem/routes.py` (`_emitir_senha`),
`app/atendimento/helpers.py` (`historico_chamadas_tv`); novo
`tests/test_regressao_sequencial_diario.py`. Suíte completa (46 arquivos) sem
falhas.

---

## 10.40. ADENDO — Etiqueta física de tamanho FIXO (7x8cm, não rolo contínuo): faixa em branco no topo + páginas extras em branco

> **⚠️ PREMISSA FÍSICA REVISTA NA 10.44** - dados novos confirmaram que esta
> impressora é na verdade um rolo contínuo de 80mm (não uma etiqueta fixa de
> 7x8cm como assumido abaixo) - a decisão do item 3 (`size: 80mm 70mm`) foi
> **superseded** pela 10.44 (`size: 80mm auto`). O registro abaixo é mantido
> como estava, sem edição retroativa - documenta o raciocínio e as evidências
> disponíveis NAQUELE momento, que motivaram a decisão então correta com a
> informação que se tinha. O colapso de altura (item 3, `visibility:hidden`)
> continua válido e foi mantido na 10.44 - só o tamanho fixo da página mudou.

> Reportado por foto comparando o cupom do sistema de referência (nítido,
> sem espaço perdido) com o cupom do GESTFILA impresso numa etiqueta real de
> banco de sangue (BSG016): faixa em branco no topo do cupom. Diferença de
> premissa importante: a etiqueta usada é de **tamanho FIXO** (7cm altura x
> 8cm largura), não uma bobina contínua - a 10.38 fixou a LARGURA (`80mm`)
> mas deixou a ALTURA em `auto`, pressupondo rolo.

**1. Causa raiz confirmada por render de impressão real** (mesma técnica da
10.38: `--print-to-pdf` do Chrome real + PyMuPDF, antes de qualquer fix):
`@page { size: 80mm auto }` tem a altura `auto` **ignorada** pelo motor de
impressão - cai no fallback padrão (Letter, 215.9x279.4mm). Uma página
virtual de quase 28cm mapeada numa etiqueta física de 7cm é o gatilho mais
provável do driver inserir avanço/espaço extra antes do conteúdo (a faixa em
branco fotografada). Com as DUAS dimensões fixas (`size: 80mm 70mm`), a
página sai corretamente em 80.1x69.8mm - confirmado por render real.
**Hipóteses alternativas descartadas por teste** (pedido explícito antes de
assumir a causa acima): renderizei tanto `auto` quanto `70mm` fixo e em
NENHUM dos dois o conteúdo saiu deslocado do topo (sempre colado em
top≈1.2mm) - não é margem herdada, não é `position:absolute` com `top` não
resetado (mesma classe de bug do `right` ignorado da 10.38, mas não é o caso
aqui). A causa é especificamente o mismatch de tamanho de página vs. etiqueta
física, que só se manifesta no driver real - por isso a confirmação final
continua sendo na impressora física (mesma ressalva da 10.38).

**2. Segundo bug, independente do primeiro - mais grave em produção que a
faixa em branco:** só apareceu ao testar a correção do item 1 (página fixa),
mas o impacto é maior: com `70mm` fixo, o cupom SEM endereço/telefone gerava
**3 páginas** (deveria ser 1); COM endereço/telefone, **4 páginas** - só a
primeira com conteúdo, as demais em branco. Numa impressora de etiqueta
física de verdade isso não é só "estética errada" como o item 1 - é
**etiqueta física desperdiçada a cada senha impressa** (2-3 etiquetas em
branco por impressão). Causa: `body * { visibility: hidden; }` (isola o cupom
na impressão) **não remove os elementos escondidos do fluxo** (`visibility`
≠ `display`) - o cabeçalho do totem, título "Sua senha", dígitos grandes,
botões etc. continuavam ocupando sua altura normal, invisível mas presente.
Com `auto`/Letter (279mm) isso nunca estourava uma página, então o bug ficava
mascarado; com a página fixa em 70mm, essa altura invisível gerava páginas
extras em branco - numa impressora de etiqueta física de verdade, isso
significa **etiquetas em branco desperdiçadas a cada senha impressa**.
Corrigido colapsando a altura do que fica escondido: `html, body { height: 0;
overflow: hidden; }` + `.totem { min-height: 0; height: 0; overflow: hidden;
}` dentro do `@media print` (o `min-height: 0` é necessário porque `.totem`
tem `min-height: 100vh` na folha base - só `height: 0` não venceria o
`min-height`). `.cupom` continua vindo via `position: absolute`, então não
depende da altura do `.totem` - confirmado que a posição não mudou.

**3. Contraste/peso da fonte (mesma foto, pedido de comparação com a
referência):** extraídas as fontes do PDF renderizado - todas as linhas do
cupom (`INGOH`, `BANCO DE SANGUE`, `BSG001`, data/hora) usam
`CourierNewPS-BoldMT`, uniformemente bold. A correção da 10.38 (peso bold
uniforme) **está** aplicada corretamente neste cupom - é o mesmo template
`senha_gerada.html` para toda fila/totem, não haveria como um cupom
específico "não ter herdado" a regra. A diferença de nitidez observada na
foto não tem causa em CSS identificável - avaliação: mais provável ser
hardware (densidade de impressão/cabeça térmica/papel), fora do escopo de
código.

**4. Decisão de arquitetura (confirmada com o cliente antes de aplicar):**
hoje não existe no código nenhuma distinção por totem/tipo de impressora (só
um campo legado `Totem.nome_impressora_windows`, não usado pela abordagem
atual via `window.print()`) - o `@page` é uma regra única, compartilhada por
todo o sistema. Medido o cupom mais alto possível no template atual (com
endereço + telefone, 8 linhas): usa 54.3mm dos 69.8mm disponíveis - cabe com
folga. Cliente confirmou que **todos os totens desta instalação usam o mesmo
tipo de etiqueta física fixa (7x8cm)** - por isso a correção foi aplicada
globalmente na regra `@page` compartilhada, sem necessidade de um campo novo
de configuração por totem. Se uma instalação futura tiver totens com tipos de
impressora diferentes (etiqueta fixa E rolo contínuo ao mesmo tempo), essa
decisão precisa ser revisitada (campo por totem, não mais uma regra global).

**5. Teste:** validado por render de impressão real (`--print-to-pdf` +
PyMuPDF) nos dois cenários (cupom curto e cupom com endereço/telefone) -
ambos saem em 1 página só, 80.1x69.8mm, conteúdo colado no topo (~1.2mm).
`tests/test_regressao_cupom_impressao_navegador.py`: casos D.2/D.4 ajustados
(janela de busca do CSS alargada de 1000 para 2000 caracteres - o bloco
`@media print` cresceu com as novas regras de colapso de altura; asserção do
`@page` trocada de `80mm auto` para `80mm 70mm`) e novo caso D.5 (colapso de
altura do conteúdo escondido). Suíte completa (46 arquivos) sem falhas. A
confirmação final na etiqueta física real fica pendente do cliente testar.

**Arquivos alterados:** `app/templates/totem/senha_gerada.html`,
`tests/test_regressao_cupom_impressao_navegador.py`.

---

## 10.41. ADENDO — Single-session enforcement no login (reaproveitando o mecanismo de logout remoto da 10.15)

> Pergunta do cliente: "o sistema permite eu logar com o mesmo usuário em
> sessões diferentes?" - resposta investigada: sim, sem nenhuma trava. Dois
> problemas de produto ficaram expostos por essa resposta:
>
> 1. **Login simultâneo sem trava nenhuma:** `login()` (`app/auth/routes.py`)
>    só LIA `Usuario.sessao_versao` pra gravar no cookie, nunca incrementava -
>    duas (ou mais) sessões do mesmo usuário conviviam normalmente, cada uma
>    batendo com o número do banco em `load_user()` (`app/__init__.py`).
> 2. **Double-booking silencioso de Local:** consequência direta do primeiro -
>    `local_atual_id`/`TurnoAtivo` (seção 10.15/10.19) são campos únicos por
>    `Usuario`, não por sessão. Duas sessões do mesmo usuário podiam escolher/
>    reconfirmar Locais diferentes (ou o mesmo) sem que uma soubesse da outra,
>    já que a regra "um usuário por Local" (10.15) só bloqueia usuários
>    DIFERENTES, nunca a própria pessoa reconfirmando - com duas sessões
>    simultâneas, "a própria pessoa" podia ser dois dispositivos brigando pelo
>    mesmo estado em silêncio.

**Decisão: "nunca deveria acontecer".** Diferente de casos onde múltiplas
sessões são um requisito real (ex.: painel de TV/totem, públicos por
natureza), aqui é uma conta de OPERADOR/ADMIN pessoal - não existe cenário
legítimo de produto para o mesmo login estar ativo em dois lugares ao mesmo
tempo (ao contrário de, por exemplo, um sistema de e-mail onde isso é
esperado). A escolha foi bloquear a causa raiz (impedir a segunda sessão de
conviver com a primeira), não tentar detectar/arbitrar o double-booking
depois que ele já aconteceu.

**1. Reuso do mecanismo da 10.15, não um mecanismo novo:** a 10.15 já tinha
construído `Usuario.sessao_versao` + a comparação em `load_user()` pro botão
"Desconectar" do admin (`admin/locais.py::desconectar_usuario_local`) -
incrementar esse mesmo número, incrementar `sessao_versao` do PRÓPRIO usuário
dentro de `login()` produz exatamente o mesmo efeito (a sessão antiga vira
inválida na próxima requisição), sem precisar de nenhuma tabela nova de
sessões ativas, nenhum Redis/cache de sessão, nenhum JWT com jti revogável -
o app já tinha tudo que precisava, só não usava no momento certo.

**2. Ordem de operações** (mesmo padrão de `desconectar_usuario_local`):
incrementa e persiste `sessao_versao` **ANTES** de gravar o cookie da sessão
que está nascendo agora - e grava no cookie o valor **JÁ INCREMENTADO** (pós-
commit, recarregado do banco). Se gravasse o valor antigo (pré-incremento), a
sessão nova nasceria já invalidada na primeira requisição seguinte, porque
`load_user()` compara contra o valor atual do banco, que já é o novo.

**2.1. Incremento ATÔMICO no SQL, não `usuario.sessao_versao = usuario.
sessao_versao + 1` em Python** (mesmo motivo já documentado em `app/totem/
routes.py::_emitir_senha`, seção 8): um read-then-write em Python não é
atômico - dois `POST /login` do MESMO usuário genuinamente simultâneos
poderiam ambos ler o valor antigo antes de qualquer um commitar, calcular o
mesmo próximo valor, e as duas sessões nasceriam válidas ao mesmo tempo
(exatamente a fresta que este mecanismo existe pra fechar). `UPDATE usuarios
SET sessao_versao = sessao_versao + 1` direto no SQL (`sqlalchemy.update`) é
serializado pelo lock de linha do MySQL/InnoDB sob concorrência real, e força
uma escrita real também no SQLite dos testes - mesmo idioma já estabelecido
em `_emitir_senha` pro mesmo tipo de risco (lost update sob concorrência).
`db.session.commit()` expira o objeto `usuario` (comportamento padrão do
SQLAlchemy) - o acesso seguinte a `usuario.sessao_versao` (pro cookie) já
recarrega do banco o valor recém-persistido, não um valor calculado em
memória.

**3. Decisão consciente: NÃO limpar `local_atual_id`/`TurnoAtivo` no login.**
Cogitado e descartado de propósito. Diferente de `desconectar_usuario_local`
(que libera o Local pra **outra pessoa** usar, porque o usuário desconectado
não volta tão cedo), aqui a sessão nova É a mesma pessoa continuando -
`local_atual_id`/`TurnoAtivo` vivem na linha do `Usuario`, não por sessão,
então a sessão nova já enxerga o mesmo estado da antiga automaticamente, sem
nenhuma limpeza necessária (não existe "sessão dona" de um Local, existe
"usuário dono"). Limpar esses campos no login forçaria o operador a re-
escolher Local/Fila toda vez que trocasse de aba/dispositivo - fricção sem
ganho real, já que o problema resolvido (double-booking) é sobre IMPEDIR a
sessão antiga de agir, não sobre resetar o estado da conta. Provado por
teste (caso F, abaixo): a sessão nova herda o turno/Local aberto pela antiga
de cara, sem precisar reconfirmar nada.

**4. Não afeta o fluxo de troca de senha obrigatória** (superadmin semeado,
`Usuario.deve_trocar_senha`): `trocar_senha_obrigatoria()` é uma rota
separada, não chama `login()` de novo - só um incremento por POST /login bem-
sucedido, sem risco de dupla invalidação. **Não afeta a guarda "admin não
pode desconectar a si mesmo"** (10.15): essa guarda vive só dentro de
`desconectar_usuario_local`, comparando contra `current_user` já autenticado
- em `login()`, `current_user` é sempre anônimo nesse ponto (a rota já
redireciona antes se já estiver autenticado), guardas de rotas diferentes,
sem sobreposição.

**5. Teste** (`tests/test_regressao_single_session.py`, novo arquivo, 15
casos): logar duas vezes com o mesmo usuário invalida a sessão antiga (302
pro login na próxima requisição) sem auto-invalidar a nova; cenário completo
do problema original (sessão A escolhe um Local, sessão B loga, sessão A
tenta agir no Local depois - é barrada ANTES de conseguir, nada muda no
banco); `Usuario.sessao_versao` realmente incrementado (não só o
comportamento HTTP observado); dois usuários DIFERENTES não se afetam;
**caso F** - sessão nova herda `local_atual_id`/`TurnoAtivo` de um turno
completo (Fila + Local) aberto pela sessão antiga, sem precisar re-escolher
nada, provando que a decisão do item 3 não deixa a conta num estado
"pendurado"; `desconectar_usuario_local` continua funcionando idêntico,
convivendo sem conflito com o novo incremento do login.
`tests/test_regressao_turno_ativo.py` tinha um caso pré-existente que
assumia duas sessões simultâneas do mesmo usuário como comportamento válido
(bloco G, "reload/nova sessão") - atualizado pra confirmar a invalidação da
sessão antiga e continuar o resto do teste com a sessão nova. Suíte completa
(47 arquivos) sem falhas.

**Arquivos alterados:** `app/auth/routes.py` (`login()`); novo
`tests/test_regressao_single_session.py`; `tests/test_regressao_turno_ativo.py`
ajustado.

---

## 10.42. ADENDO — Investigação "impressão saindo em paisagem" (código confirmado correto - suspeita de driver Windows)

> Reportado pelo cliente após a 10.40: cupom saindo em modo paisagem em
> TODOS os totens (hoje 2), pedido de padronizar em retrato. Contradição
> aparente a resolver antes de tocar em qualquer CSS: a 10.40 fixou `@page
> { size: 80mm 70mm }` - largura (80mm) MAIOR que altura (70mm), ou seja, a
> página já é "paisagem" por definição de forma desde aquele fix.

**1. Código investigado, confirmado correto e inalterado desde a 10.40:**
grep em `senha_gerada.html`, `_layout.html` e `gestfila.css` por
`orientation`/`rotate`/`landscape`/`portrait`/`transform` - nenhuma
ocorrência real (só um falso positivo de `text-transform`, propriedade
diferente). Nenhuma regra de rotação esquecida em nenhum lugar do sistema.

**2. Render de impressão real** (`--print-to-pdf` do Chrome + PyMuPDF, mesma
técnica da 10.38/10.40): `MediaBox: 227.04 x 198.0 pts` = **80.09 x
69.85mm**, `rotation: 0`. O código pede exatamente o que a 10.40 documentou -
sem rotação, sem fallback pra página grande tipo Letter/A4 (que apareceria
como 215.9x279.4mm, o sintoma que a 10.40 já corrigiu uma vez). Conteúdo
ocupa só ~40mm dos 70mm disponíveis, sem overflow.

**3. Confirmado com o cliente: 8cm É a largura fixa real do rolo/suporte da
impressora** (não uma inversão de medidas na 10.40). Isso fecha a
possibilidade de "corrigir" trocando pra `size: 70mm 80mm` - isso
contrariaria a largura física real e pioraria a impressão, não consertaria.

**4. Conclusão: não há bug de código pra corrigir aqui.** O `@page` já pede
o tamanho fisicamente correto (80x70mm, casando com a largura real do rolo),
sem rotação, validado por render real. A foto do cliente mostra uma tira
visivelmente mais comprida que os 70mm esperados - como o `--print-to-pdf`
só prova o que o CSS *pede* (não o que o driver da impressora *faz* com esse
pedido - ressalva já registrada na 10.28/10.40 e nunca totalmente validada em
hardware físico), a explicação mais provável é o **driver Windows não
respeitando o `@page size`** do CSS na impressão real - explica também por
que acontece nos DOIS totens (mesma configuração de driver replicada nos
dois, não algo em comum no código, que já está confirmado correto e
idêntico nos dois).

**5. Nenhum código alterado.** Decisão tomada com o cliente: documentar o
diagnóstico e orientar checagem de configuração do driver, em vez de
alterar CSS que já está correto. Checklist pra conferir na impressora física
de cada totem (Configurações de impressora do Windows, ou no diálogo de
impressão se `--kiosk-printing` não estiver ativo - ver procedimento da
10.38):
- **Tamanho de papel no driver:** deve ser 80x70mm (ou o tamanho
  personalizado equivalente) - **não** Letter/A4/Carta nem "Ajustar
  automaticamente".
- **Orientação no driver:** Retrato, mesmo a etiqueta sendo mais larga que
  alta - o `@page size: 80mm 70mm` já define a forma da página pelo CSS;
  forçar "Paisagem" no driver por cima disso pode fazer o driver rotacionar
  de novo, dobrando o efeito.
- **Escala:** 100%, nunca "Ajustar à página" (mesma orientação já dada na
  10.38).
- Se o driver não tiver opção de tamanho de papel personalizado 80x70mm,
  criar um tamanho de papel customizado nas propriedades da impressora
  (Painel de Controle > Dispositivos e Impressoras > Propriedades do
  Servidor de Impressão > Criar um Novo Formulário).

**Se o checklist acima não resolver:** o próximo passo de diagnóstico seria
capturar o job de impressão bruto (spool) que o driver realmente envia pra
impressora, pra comparar contra o que o `--print-to-pdf` mostrou aqui - fora
do escopo desta investigação (exige acesso físico ao totem), fica registrado
como próximo passo se o ajuste de driver não for suficiente.

**Arquivos alterados:** nenhum (investigação e documentação apenas).

---

## 10.43. ADENDO — Upload de logo por empresa (cupom + painel de TV + admin)

> Feature nova: cada empresa (multi-tenant) pode fazer upload de um logo
> próprio, exibido em três lugares - cupom impresso do totem, painel de TV,
> e admin. Mesmo nível de escopo do "Nome da empresa" já existente (seção 8:
> vem do cadastro de Empresa), não por unidade/totem.

**1. Armazenamento:** `Empresa.logo_filename` (nullable, migration
`6f6d09c50b0d`) guarda só o NOME do arquivo (ex.: `empresa_7.png`), nunca o
caminho completo nem o blob no banco. Arquivo físico em
`app/static/uploads/logos/`, nomeado deterministicamente como
`empresa_<id>.png` - **sempre esse nome, sempre sobrescrevendo**, nunca
baseado no nome original enviado pelo usuário (elimina por construção
qualquer risco de colisão de nome entre empresas ou de path traversal via
nome de arquivo malicioso). Pasta gitignorada (`app/static/uploads/`) -
dado de instalação, não asset versionado do código-fonte, mesmo espírito de
`instance/`.

**2. Validação por CONTEÚDO real, não extensão/Content-Type**
(`app/utils/uploads.py::salvar_logo_empresa`): usa Pillow (`Image.open()` +
`.verify()`) pra de fato decodificar o arquivo e confirmar que é PNG ou JPEG
genuíno - a extensão do nome enviado e o `Content-Type` do upload são
controlados pelo cliente e não provam nada sozinhos. **SVG recusado de
propósito**: é XML, pode embutir `<script>`/`onload` executável quando
aberto num navegador (risco de XSS) - diferente de PNG/JPG, que são dados
binários de pixel sem capacidade de execução. Reencodar sempre para PNG
(mesmo quando o original é JPG) funciona também como sanitização: qualquer
coisa fora dos pixels que a Pillow decodificou (metadados, payload de
polyglot anexado ao arquivo) é descartada no processo. `Image.verify()` +
`Image.MAX_IMAGE_PIXELS` (limite padrão do Pillow, já ligado) protegem
contra decompression bomb (arquivo pequeno que decodifica pra uma imagem
gigante). **Limite de tamanho: 2MB** (`TAMANHO_MAXIMO_LOGO_BYTES`), validado
na aplicação; `Config.MAX_CONTENT_LENGTH = 3MB` no Flask como defesa em
profundidade ANTES disso (evita bufferizar em memória um upload
absurdamente grande antes mesmo do código da aplicação rodar).

**3. Permissão: só super admin** (`@super_admin_required`, mesmo decorator
das outras 5 rotas de Empresa) - **não** "admin da própria empresa" como a
premissa inicial do pedido assumia. Confirmado antes de implementar: TODAS
as rotas de cadastro de Empresa hoje (`listar/nova/editar/toggle/excluir`)
já são exclusivas de super admin - gerenciar o cadastro da EMPRESA em si
(diferente de gerenciar unidades/usuários/filas DENTRO dela, que aí sim é
admin local) nunca foi acessível a admin de empresa neste sistema. O upload
de logo segue o padrão já existente, não abre uma exceção nova.

**4. Botão de remoção** (`POST /admin/empresas/<id>/logo/remover`): apaga o
arquivo físico e limpa `logo_filename` - volta a exibir só o nome em texto
nos 3 lugares, sem precisar substituir por outro arquivo pra "desfazer".

**5. Exibição com fallback nos 3 lugares** - sem logo cadastrado, cada tela
continua EXATAMENTE como antes desta feature, nenhuma mudança visual:
- **Cupom** (`totem/senha_gerada.html`): `<img class="cupom-logo">` acima do
  nome da empresa (layout da seção 8), dentro de `.cupom` - já herda a
  visibilidade/posicionamento do `@media print` existente (10.40), nenhuma
  regra nova de impressão necessária. **Limitação de hardware documentada,
  não bug de código** (mesmo espírito da ressalva de validação em
  impressora real, 10.38/10.40): impressora térmica é monocromática e de
  baixa resolução - um logo com muito detalhe/gradiente pode sair
  borrado/ilegível dependendo do arquivo original: aviso explícito no
  formulário de upload do admin.
- **Painel de TV** (`painel/tv.html` + `painel/_layout.html`): logo ao lado
  da marca GESTFILA no cabeçalho (`.cabecalho-marca`, já `display:flex`).
- **Admin**: miniatura na listagem de empresas + preview no formulário de
  edição (só na edição, não na criação - o nome de arquivo determinístico
  precisa do ID, que só existe depois de a empresa já ter sido criada).

**6. Isolamento multi-tenant:** nome de arquivo determinístico por
`empresa_id` evita colisão/mistura por construção (upload pra uma empresa
fisicamente não pode escrever no arquivo de outra). **Decisão consciente
sobre acesso direto à URL do arquivo:** os arquivos ficam em pasta pública
(`app/static/`), então a URL do logo de qualquer empresa é acessível sem
login, se alguém souber/adivinhar o ID - isto NÃO é tratado como falha de
isolamento: é um asset público por natureza (mesmo nível de "publicidade"
que as páginas de totem/painel de TV, que também são públicas sem login) -
um logo de empresa não é dado sensível. O que de fato importa (e é
garantido) é nunca vazar o ARQUIVO ou CAMPO errado entre tenants, não
restringir acesso a uma imagem de marca.

**7. Teste** (`tests/test_regressao_logo_empresa.py`, novo arquivo, 14
casos): upload válido PNG e JPG (normalizado pra `.png` nos dois casos),
rejeição de SVG disfarçado de `.png` por conteúdo real (não extensão),
rejeição por tamanho, remoção, fallback nos 3 lugares com e sem logo,
isolamento entre duas empresas (upload numa não cria/altera nada na outra),
e RBAC (admin de empresa recebe 403, confirmando o item 3). Suíte completa
sem falhas.

**Arquivos alterados:** `migrations/versions/6f6d09c50b0d_*.py` (novo),
`app/models/empresa.py` (`logo_filename`), `app/utils/uploads.py` (novo),
`app/admin/empresas.py` (upload + rota de remoção), `app/templates/admin/
empresas/form.html` e `listar.html`, `app/templates/totem/senha_gerada.html`,
`app/templates/painel/tv.html` e `_layout.html`, `app/config.py`
(`MAX_CONTENT_LENGTH`), `requirements.txt` (`Pillow`), `.gitignore`
(`app/static/uploads/`); novo `tests/test_regressao_logo_empresa.py`.

---

## 10.44. ADENDO — `@page` volta pra altura "auto": premissa física da 10.40 estava errada (rolo contínuo, não etiqueta fixa)

> **Decisão revista, não "vai-e-volta" por capricho.** A 10.40 fixou `@page
> { size: 80mm 70mm }` em cima da premissa de que a impressora usa uma
> etiqueta física de tamanho FIXO (7cm altura x 8cm largura). Dados novos
> confirmados nesta sessão contradizem essa premissa: a impressora real é um
> **rolo contínuo de 80mm de largura**, formato retrato, com **corte
> automático rente ao conteúdo** - o mesmo tipo de impressora de senha usada
> pela Caixa Econômica (referência visual enviada pelo cliente confirmando o
> formato). Evidência decisiva: **o driver Windows desta impressora só
> oferece a opção de papel 80x297mm** - papel de rolo contínuo padrão, nunca
> um tamanho fixo de 70mm de altura. A 10.40 não estava errada em *querer*
> resolver o problema da faixa em branco - estava errada na suposição física
> sobre QUAL hardware estava sendo configurado.

**1. Investigação (fase 1) - tentativa de reproduzir uma SEGUNDA causa antes
de mexer em CSS:** antes de reverter a decisão da 10.40, investigado se
havia uma causa de código adicional (não só a premissa física errada) pro
problema relatado em produção. Testado via `--print-to-pdf` real (mesma
técnica da 10.38/10.40/10.42), cenário exato do incidente relatado (fila
"Recepção Banco de Sangue", unidade com endereço E telefone - o conteúdo
mais alto do template):

```
atual_80x70_com_header:    paginas=1  80.09x69.85mm
atual_80x70_sem_header:    paginas=1  80.09x69.85mm
proposto_auto_com_header:  paginas=1  215.90x279.40mm (fallback Letter, esperado no headless)
proposto_auto_sem_header:  paginas=1  215.90x279.40mm
```

**Não foi possível reproduzir uma segunda página em nenhuma combinação**,
com ou sem cabeçalho/rodapé do navegador ligado. Também confirmado que não
existe nenhum elemento fora de `.totem` em `totem/_layout.html` (`<body>`
só tem `.totem`, que envolve tudo, inclusive o logo GESTFILA) e que
`.gf-watermark` (usado no painel de TV) não é referenciado em nenhum
template do totem. **Conclusão da fase 1:** nenhuma segunda causa de código
encontrada em ambiente controlado - o colapso de altura da 10.40 (mantido,
ver item 3) já garante 1 página sozinho, independente do `@page size`. Se
alguma página extra ainda foi vista fisicamente em produção antes desta
correção, a explicação mais provável é a mesma da 10.42 (driver não
respeitando um `@page size` fixo que não correspondia ao hardware real) -
**fica registrado como não totalmente explicado em ambiente controlado**,
pendente de confirmação num teste físico real depois deste deploy.

**2. Mudança:** `@page { size: 80mm 70mm; margin: 0; }` → `@page { size:
80mm auto; margin: 0; }` (`app/templates/totem/senha_gerada.html`). Volta a
ser o comportamento CERTO pra rolo contínuo: o corte automático da
impressora decide onde a etiqueta termina, não uma altura fixa arbitrária
no CSS. Validado por render real (`--print-to-pdf` + PyMuPDF): 1 página,
conteúdo colado no topo (~1.2mm), sem overflow, tanto pro cupom curto
quanto pro cenário real (Recepção Banco de Sangue, endereço+telefone,
conteúdo terminando em ~50mm).

**3. O que NÃO mudou - colapso de altura da 10.40, mantido integralmente:**
`html, body { height: 0; overflow: hidden; }` + `.totem { min-height: 0;
height: 0; ... }` dentro do `@media print` continuam necessários
independente do tamanho de página - sem eles, o cabeçalho/título/dígitos/
botões escondidos (`visibility:hidden` não tira do fluxo) desperdiçariam
papel de rolo imprimindo espaço vazio antes do corte automático, mesmo com
`size: auto` (o rolo seguiria alimentando até acabar o conteúdo REAL da
página, que incluiria essa altura invisível). Este mecanismo não dependia
da premissa física errada da 10.40 - continua correto.

**4. Nota sobre o histórico - 10.38/10.40 marcadas como SUPERSEDIDAS, não
apagadas:** a decisão da 10.40 (`size: 80mm 70mm`) foi tomada de boa-fé,
com o melhor entendimento disponível na época (relato do cliente + medidas
que pareciam confirmar uma etiqueta fixa) - o registro de POR QUE aquela
decisão foi tomada continua no BLUEPRINT, sem edição retroativa. O que este
adendo faz é registrar que dados novos (driver só oferece 80x297mm,
referência visual da impressora real da Caixa) contradisseram a premissa, e
documentar a correção com o mesmo rigor. A largura fixa de 80mm decidida na
**10.38** continua válida e não foi revista - só a altura (`auto` vs. fixa)
mudou.

**5. Teste** (`tests/test_regressao_cupom_impressao_navegador.py`): D.4
trocado de volta pra aceitar `@page { size: 80mm auto` e recusar `80mm
70mm` (sanity check manual: revertido temporariamente pro valor antigo,
confirmado que o teste quebra na asserção certa, restaurado). D.5 (colapso
de altura) mantido sem alteração - continua válido. Novo caso D.6 reproduz
o cenário real do incidente (fila "Recepção Banco de Sangue", unidade com
endereço+telefone) - estrutural (confirma o HTML/CSS certos chegam pro
navegador), não confirma "1 página" literalmente: isso exige motor de
impressão real, e este projeto deliberadamente não depende de Chrome/
PyMuPDF na suíte automatizada (mesmo padrão das validações manuais da
10.38/10.40/10.42, nunca comitadas como teste - decisão confirmada
com o cliente nesta sessão, não uma omissão). A confirmação de "1 página"
pra este caso é a investigação da fase 1 (item 1 acima), rodada manualmente
e documentada aqui. Suíte completa sem falhas.

**Arquivos alterados:** `app/templates/totem/senha_gerada.html`,
`tests/test_regressao_cupom_impressao_navegador.py`.

---

## 10.45. ADENDO — Campos opcionais nulos apareciam como a string "None" (cupom mostrando "None"/"None")

> Reportado pelo cliente: cupom impresso mostrando literalmente a palavra
> "None" no lugar do endereço/telefone quando esses campos não estavam
> preenchidos para aquela unidade.

**1. Causa raiz - não é bug no cupom, é bug no FORMULÁRIO DE EDIÇÃO:** o
template `totem/senha_gerada.html` já fazia o `{% if unidade.endereco %}`
certo desde sempre (confirmado lendo o código antes de mexer - não vaza
"None" quando o campo é `NULL` de verdade). O bug real está nos formulários
de edição do admin: `{{ campo if objeto else '' }}` só trata o caso "o
OBJETO inteiro é `None`" (tela de "Novo/Nova", sem registro ainda) - não
trata o caso "o objeto existe, mas o CAMPO em si é `None`" (registro
existente, campo opcional nunca preenchido). Nesse segundo caso, o Jinja
renderiza a string literal `"None"` dentro do `value=""`/`<textarea>`. Se o
admin abrisse o formulário e salvasse sem tocar nesse campo específico
(cenário comum - só queria editar outra coisa), o texto `"None"` (uma
string NÃO-vazia) sobrevivia ao `.strip() or None` do backend e virava o
valor de **verdade** no banco, substituindo o `NULL` original. O cupom
então mostrava "None" porque o campo genuinamente passou a conter essa
string - o `{% if %}` do cupom nunca teve chance de filtrar, porque não
havia mais nada pra filtrar.

**2. Confirmado com dado real de produção antes de corrigir** (mesmo
padrão de investigação desta sessão - nunca assumir, sempre checar o banco
real primeiro): `unidades.telefone` (id 3, "Laranjeiras") e
`totens.nome_impressora_windows` (ids 1, 2, 3) já estavam com a string
`"None"` gravada. A unidade "Banco de Sangue" (a do cupom da foto) já
estava com `NULL` de verdade no momento da investigação - consistente com
alguém ter aberto o formulário, visto o texto "None" no campo, apagado
manualmente e salvo (o que corrige por acidente aquele registro específico
via a lógica correta do backend, sem tocar na causa raiz no template).

**3. Escopo do bug - 5 campos opcionais em 5 formulários, dois níveis de
gravidade:**
- **Corrompem dado de verdade** (string não-vazia sobrevive ao `.strip() or
  None`): `Unidade.endereco`/`telefone`, `Empresa.cnpj`,
  `Totem.nome_impressora_windows`. Corrigidos com `(campo if objeto else
  '') or ''`.
- **Só cosmético** (`Fila.tempo_espera_maximo_min`/
  `tempo_atendimento_maximo_min`, `TipoSenha.tempo_maximo_min`): o backend
  lê com `request.form.get(..., type=int) or None` - o Werkzeug descarta
  silenciosamente `"None"` como texto inválido pra `int` e grava `NULL`
  mesmo assim, então o dado nunca corrompia de verdade, só a UX ficava
  confusa mostrando "None" no campo numérico. Corrigidos com `is not none`
  (não `or ''` - um valor `0` legítimo é falsy em Python/Jinja, `or`
  trocaria por `''` incorretamente pra um campo numérico).

**4. Limpeza dos dados já corrompidos:** as linhas com `"None"` literal
identificadas no item 2 foram corrigidas direto no banco (`UPDATE ... SET
campo = NULL WHERE campo = 'None'`), restaurando o estado que sempre
deveria ter existido - não é dado novo, é a correção do valor que o bug
gravou por engano.

**5. Teste** (`tests/test_regressao_crud_admin_empresas_unidades.py`, 2
casos novos): GET no formulário de edição de Unidade/Empresa com campo
opcional `None` confirma que a string "None" não aparece no HTML
renderizado (nem no `<textarea>`, nem no `value=""`); round-trip completo
(GET + POST simulando "admin salva sem tocar no campo") confirma que o
banco continua com `NULL` de verdade depois de salvar, não a string
"None". Sanity check manual: revertida cada correção temporariamente,
confirmado que a asserção certa quebra (a primeira tentativa da asserção
do CNPJ tinha a ordem de atributos HTML errada - `id="cnpj" value="None"`
em vez de `name="cnpj" value="None"` - corrigida antes de confirmar o
sanity check de verdade). Suíte completa sem falhas.

**Arquivos alterados:** `app/templates/admin/unidades/form.html`,
`app/templates/admin/empresas/form.html`, `app/templates/admin/totens/
form.html`, `app/templates/admin/filas/form.html`, `app/templates/admin/
tipos_senha/form.html`; `tests/test_regressao_crud_admin_empresas_unidades.py`
ajustado.

---

## 10.46. ADENDO — Impressão silenciosa (kiosk) saía sem CSS: `<style>` do cupom precisa vir ANTES do `<script>`

> Reportado pelo cliente: com a impressão silenciosa ativada (kiosk printing
> / `print.always_print_silent` no Firefox, `--kiosk-printing` no
> Chrome/Edge - sem o diálogo nativo do navegador), a etiqueta saía
> desconfigurada, sem a formatação CSS do cupom. Com o diálogo de impressão
> normal, saía correta. Investigação pedida contra 5 hipóteses específicas
> (corrida de carregamento de CSS, tamanho/margem de `@page`,
> `background-color`/`print-color-adjust`, disparo pelo Socket.IO antes do
> paint, `@media print` escondendo o nó errado) - relatada ao cliente com
> arquivo:linha ANTES de qualquer correção ser aplicada, só corrigido depois
> de aprovação explícita.

**1. Causa raiz - corrida de parsing HTML, não rede/webfont:** em
`app/templates/totem/senha_gerada.html`, o bloco `<script>` do cupom (que
chama `window.print()` de forma SÍNCRONA já no carregamento inicial, dentro
de um guard de `sessionStorage` que evita imprimir a mesma senha duas
vezes) vinha ANTES do bloco `<style>` (que tem as regras de `.cupom`,
`@page`, `@media print`). O parser HTML do navegador executa `<script>`
assim que o encontra, ANTES de processar o resto do documento que vem
depois dele - então `window.print()` podia disparar antes do parser sequer
ter chegado nas regras do `<style>`, com o CSSOM do cupom ainda incompleto.
Não é bug de rede/webfont: todo o CSS deste cupom é inline (sem `<link>`
externo nem `@font-face` - "Courier New" é fonte de sistema), então não
tem nada "carregando" de fora pra dar corrida com um `setTimeout` ou
`link.onload`.

**2. Por que só aparecia sem diálogo:** com o diálogo nativo de impressão,
a espera pelo clique humano no botão "Imprimir" dá folga de sobra pro
parser terminar de processar o resto do documento (inclusive o `<style>`)
muito antes da impressão de fato renderizar - a corrida existia no código,
mas nunca tinha chance de se manifestar visivelmente. Sem diálogo
(impressão silenciosa), a impressão acontece o mais rápido possível assim
que `window.print()` é chamado, sem essa folga - a corrida vira bug visível
com frequência alta.

**3. 4 das 5 hipóteses do cliente descartadas com evidência direta:**
`@page` (tamanho/margem) já estava correto e sem `named page` conflitante
(10.44); não há dependência de `background-color`/`print-color-adjust` no
CSS do cupom (regras usam texto bold em vez de cor de fundo, herdado da
correção de contraste da 10.38); a chamada de `window.print()` não depende
de nenhum evento do Socket.IO, só do carregamento inicial da própria página
do cupom; o `@media print` esconde o nó certo (`.totem` inteiro, exceto
`.cupom` - mesma regra testada desde a 10.38). A quinta hipótese (corrida
de carregamento de CSS) se confirmou, mas numa variante diferente da
levantada pelo cliente (CSS externo/`<link>`/webfont) - a real é ordem de
parsing entre `<script>` síncrono e `<style>` inline no mesmo documento.

**4. Correção - só reordenar, sem `setTimeout`/`await` novo:** o bloco
`<style>` foi movido pra ANTES do `<script>` no template (o HTML/CSS em si
não mudou, só a ORDEM dos dois blocos). Isso garante que o CSSOM do cupom
esteja completo antes de qualquer script rodar, incluindo o
`window.print()` automático. Não muda nada no modo com diálogo (que já
tinha folga de sobra) - só fecha a corrida que, nesse modo, nunca chegava a
se manifestar.

**5. Teste** (`tests/test_regressao_cupom_impressao_navegador.py`, caso
C.1 novo): confirma que o `<style>` do CUPOM vem antes do `<script>` do
cupom no HTML renderizado. Armadilha encontrada e corrigida durante o
próprio sanity check desta correção: a primeira versão da asserção usava
`texto.index("<style>")` (primeira ocorrência no documento inteiro), que
sem querer batia no `<style>` de `_layout.html` (dentro do `<head>`,
sempre antes de qualquer conteúdo do `<body>` independente da ordem local
do cupom) - a asserção "passava" mesmo com o bug revertido de propósito
pra teste. Corrigido pra `texto.rindex("<style>")` (ÚLTIMA ocorrência no
documento): como `_layout.html` só tem um `<style>` e o cupom só tem um
`<style>` e um `<script>`, e o do layout vem sempre primeiro (é do
`<head>`), o último `<style>` do documento é necessariamente o do cupom,
esteja ele antes ou depois do `<script>`. Sanity check refeito depois da
correção do teste: revertida a ordem de propósito, a asserção quebrou
como esperado; restaurada a ordem certa, os 19 testes do arquivo voltaram
a passar. Suíte completa (~49 arquivos) sem falhas.

**Arquivos alterados:** `app/templates/totem/senha_gerada.html` (só
reordenação de blocos, sem mudança de conteúdo);
`tests/test_regressao_cupom_impressao_navegador.py` (caso C.1 novo).

---

## 10.47. ADENDO — Sigla passa de `Fila` para `TotemBotao` (realinha com a seção 2 original)

> Mudança de arquitetura confirmada com o cliente: (1) a sigla usada na
> composição do código da senha (seção 2) deixa de pertencer à Fila e passa a
> pertencer ao Botão do totem - permite no futuro dois botões diferentes
> apontarem pra mesma fila com siglas diferentes; (2) a tela de filas continua
> permitindo criar fila SEM botão vinculado (fila órfã), com a sigla ficando
> pendente até algum botão ser vinculado a ela.

**1. Isso realinha com a intenção original da seção 2, perdida na fusão da
10.1:** o texto original do BLUEPRINT já dizia "cada serviço tem uma sigla
configurável, definida pelo admin **ao criar o botão do totem**" - mas a
fusão de Serviço em Fila (10.1) migrou a sigla para `Fila.sigla` junto com o
resto dos campos de `servicos`, sem essa distinção. Este adendo devolve a
sigla para onde a seção 2 sempre disse que ela deveria estar.

**2. `TotemBotao.sigla`** (nova coluna, `VARCHAR(4)`, nullable): só obrigatória
em botões-FOLHA (`fila_id` preenchido) - botões-menu (ex.: "MEDICOS") nunca
têm sigla, mesma convenção já usada para `fila_id` desde a 10.1.

**2.1. Unicidade — achado real de produção corrigiu a primeira versão desta
migration:** a primeira tentativa criava uma `UNIQUE(unidade_id, sigla)` de
banco (`uq_totem_botao_unidade_sigla`), espelhando a antiga `uq_fila_unidade_
sigla`. Ao rodar `flask db upgrade` em produção, ela quebrou: a fila "Recepção
Banco de Sangue" já tinha **5 botões-folha diferentes** ("Doação de
Plaquetas", "Preferencial", "Doação de Sangue", "Resultados", "Sangria")
apontando pra ela, todos compartilhando de propósito a mesma sigla "BSG" (mesma
fila, mesmo sequencial, mesmo prefixo - configuração legítima, não duplicidade
acidental). A migração de dados (item 4) copia `fila.sigla` pra cada um desses
5 botões igualmente, e a constraint nova rejeitava a segunda linha em diante.
A regra que faz sentido não é "todo botão precisa de sigla exclusiva", é
**"duas FILAS diferentes não podem usar a mesma sigla na mesma unidade"**
(código ambíguo entre filas de verdade diferentes) - o que não é uma unique
key simples, já que múltiplos botões da MESMA fila podem legitimamente
repetir a sigla dela. Correção: a migration não cria mais nenhuma unique
constraint de banco em `totem_botoes`; a checagem (sigla já usada por um
botão de OUTRA fila na mesma unidade) fica só em `app/admin/totem_botoes.py::
_validar_totem_botao`. Prova em `tests/test_regressao_sigla_botao_totem.py`
(caso E): sigla repetida entre FILAS diferentes continua bloqueada; sigla
repetida entre botões da MESMA fila é permitida (replica o caso real do
Banco de Sangue). `unidade_id` já existe diretamente em `TotemBotao` desde
sempre (não precisou navegar por `fila` pra resolver o escopo da checagem).

**3. `Fila.sigla` fica DEPRECIADA** (mesmo padrão da tabela `servicos` na
10.1): coluna mantida "morta" por 1 deploy, virou nullable (fila pode ser
criada/salva sem sigla e sem botão vinculado - órfã de verdade), não é mais
lida nem escrita por nenhum código novo. Remoção da coluna prevista num
adendo futuro, depois de confirmar a migração limpa em produção.

**4. Migração de dados** (`migrations/versions/7a2e1c4f9b3d_...py`): para cada
`totem_botao` folha existente (`fila_id IS NOT NULL`), copia `fila.sigla` pro
novo `totem_botao.sigla` - ponto de partida, nenhuma sigla já cadastrada se
perde.

**5. `_emitir_senha`/`_compor_codigo`** (`app/totem/routes.py`): passam a
receber o `botao` clicado (sempre disponível - toda geração de senha no totem
passa por um botão-folha específico) e usam `botao.sigla` na composição do
código, não mais `fila.sigla`. O contador/sequencial **continua** escopado
por `fila_id + unidade + dia`, exatamente como na 10.39 - não muda. Efeito
intencional: se dois botões diferentes (siglas diferentes) apontarem pra
mesma fila, o sequencial numérico é COMPARTILHADO entre eles (nunca duplica
um número), só o prefixo de sigla muda conforme o botão usado pra gerar -
provado em `tests/test_regressao_sequencial_diario.py` (caso I).

**6. Bloqueio defensivo:** se um botão-folha tiver `fila_id` mas `sigla`
vazia (não deveria acontecer, já que o formulário de botão passa a exigir a
sigla nesse caso - ver item 8), a geração é bloqueada com a mesma tela/
mensagem padrão de "atendimento não configurado" já usada em `app/templates/
totem/erro_configuracao.html`, em vez de gerar um código corrompido.

**7. Admin - tela de filas:** campo sigla REMOVIDO do formulário (`app/
templates/admin/filas/form.html`) - fila não exige mais sigla pra ser criada/
salva. Coluna "Sigla" removida da listagem de filas também (dado agora vive
no botão).

**8. Admin - tela de botões:** campo sigla ADICIONADO ao formulário (`app/
templates/admin/totem_botoes/form.html`), obrigatório e validado (2-4 letras
A-Z, mesma regra de sempre) quando uma fila é selecionada; descartado
(sempre `None`) quando o botão é de navegação (sem fila). Vincular uma fila a
um botão sem preencher a sigla bloqueia o salvamento com mensagem clara.

**9. Testes:** `tests/test_regressao_sequencial_diario.py` (fixture `nova_fila`
passa a criar um botão-folha companheiro; caso I novo: dois botões, mesma
fila, siglas diferentes, sequencial compartilhado sem colisão) e o novo
`tests/test_regressao_sigla_botao_totem.py` (sigla obrigatória/formato/
unicidade no CRUD de botão, botão-menu nunca exige sigla, geração usa a sigla
do botão e não da fila deprecada, fila órfã de sigla e botão, migração de
dados simulada, RBAC de edição de botão). Os 8 arquivos de teste que já
exercitavam geração de senha via botão-folha ganharam `sigla=` nas fixtures
de `TotemBotao` para continuar batendo os códigos esperados.

**10. Risco de concorrência ACEITO conscientemente, não fechado (diferente da
10.41):** `_validar_totem_botao` (item 2.1) faz um `SELECT` de conflito e o
`INSERT`/`UPDATE` do botão acontece num commit separado, sem lock de linha,
sem `UPDATE` atômico e sem tratamento de `IntegrityError` - é um TOCTOU
clássico. Duas requisições `POST /admin/totem-botoes/novo` (ou `/editar`)
genuinamente simultâneas, para FILAS DIFERENTES com a MESMA sigla na mesma
unidade, podem ambas passar pelo `SELECT` antes de qualquer uma commitar, e
as duas gravações acontecem - o app-level check não pega essa janela. E,
diferente da 10.41 (que tinha um `UPDATE` atômico como saída direta), aqui
NÃO existe mais nenhuma rede de segurança no banco: a constraint de banco
(item 2.1) foi removida de propósito, porque ela também bloqueava o caso
legítimo de vários botões da MESMA fila compartilhando sigla (Banco de
Sangue) - uma unique key simples não consegue expressar "só entre filas
diferentes", então não há substituto de banco pronto pra fechar esta fresta.

**Por que aceito sem fix agora:** cadastro de botão do totem é uma ação
administrativa rara (não é um fluxo de alto tráfego como emissão de senha,
`_emitir_senha`, que TEM lock de linha real - seção 8) - dois admins da
mesma empresa cadastrando botões-folha com a mesma sigla, para filas
diferentes, no mesmo instante, é uma janela de colisão extremamente estreita
na prática. O pior caso de uma colisão não detectada é um código de senha
ambíguo entre duas filas (mesmo problema que a validação existe pra evitar,
só que sem ser pega) - não perda de dado, não segurança, não duplicação de
senha (isso continua protegido por `_emitir_senha`). Se algum dia isso virar
um fluxo de alto tráfego ou multi-operador simultâneo, fechar com um dos
padrões já estabelecidos no projeto: `SELECT ... FOR UPDATE`/`UPDATE` atômico
bloqueando a fila em questão antes do `SELECT` de conflito (mesmo idioma da
seção 8/10.41), ou capturar `IntegrityError` de uma constraint de banco mais
específica (ex.: um índice único parcial/filtrado, se o dialeto usado em
produção suportar - MySQL não suporta unique index parcial nativamente, só
via coluna gerada/trigger, o que precisaria de investigação própria antes de
implementar).

**Arquivos alterados:** `migrations/versions/7a2e1c4f9b3d_sigla_passa_de_
fila_para_totem_botao.py` (nova); `app/models/totem_botao.py`, `app/models/
fila.py`; `app/totem/routes.py`; `app/admin/filas.py`, `app/admin/
totem_botoes.py`; `app/templates/admin/filas/form.html`, `app/templates/
admin/filas/listar.html`, `app/templates/admin/totem_botoes/form.html`,
`app/templates/admin/totem_botoes/listar.html`; testes listados no item 9.

---

## 10.48. ADENDO — Corte de início da fala no painel de TV em hardware fraco (Celeron + 4GB)

> Reportado: a fala da chamada (painel de TV) corta o INÍCIO em TODA
> chamada (não só a primeira do dia), em TODOS os navegadores, mas só numa
> máquina específica (Celeron + 4GB RAM) - outras máquinas mais potentes não
> têm o problema. Esse padrão descarta o bug clássico de "cold start" do
> Chromium em `speechSynthesis` (só afeta a primeira fala após período
> ocioso - já registrado como descartado/revertido na 10.13, não
> reintroduzido aqui) e aponta pra hardware fraco disputando CPU no momento
> exato em que a fala deveria começar.

**1. Investigação (código lido, sem runtime de navegador disponível neste
ambiente - mesma limitação já registrada em outros testes de JS do
projeto):** em `painel/tv.html`, o handler do evento `chamada` rodava
`inserirChamada(dados)` (atualização visual) e `falarChamada(dados)`
(`speechSynthesis.speak()`) **no mesmo tick síncrono, sem nenhum
`setTimeout`/`requestAnimationFrame` entre os dois.** Antes de `speak()` ser
sequer chamado, `inserirChamada()` já tinha disparado
`dispararAnimacaoDestaque()`, que:
- força um **reflow síncrono** (`void elChamadaAtual.offsetWidth`) -
  bloqueia a thread principal até o navegador recomputar layout, em TODA
  chamada (não só a primeira), igual em qualquer engine (Chromium/Gecko) -
  bate com o padrão relatado ("todo navegador, toda chamada");
- dispara uma animação CSS de 3s (`chamada-destaque`,
  `app/templates/painel/_layout.html`) que anima **`box-shadow`** -
  propriedade que força repaint a cada frame (não é compositor-only como
  `transform`/`opacity`, usada na outra animação do mesmo elemento), bem
  mais cara num Celeron do que numa máquina forte.

Além disso, `vozPtPreferida()` (seleção de voz, 10.13) era recalculada do
zero (`filter()` + `sort()`) em **toda** chamada, dentro do mesmo caminho
síncrono - trabalho redundante, já que o resultado só muda quando a lista de
vozes do navegador muda (evento `voiceschanged`). E `falarChamada()` fazia
`speechSynthesis.cancel()` imediatamente seguido de `speechSynthesis.speak()`
no mesmo tick - padrão com bug conhecido (não confirmado neste caso
específico) no Chromium, onde o `speak()` novo pode começar a ser processado
antes do cancelamento anterior propagar internamente, cortando o início do
áudio novo.

**Causa mais provável, por eliminação e pelo padrão exato relatado:** o
reflow forçado + a animação de `box-shadow` de 3s, disparados no mesmo tick
síncrono imediatamente antes de `speak()`, competem por CPU bem no momento
em que a fala deveria começar - em hardware fraco essa janela de contenção é
grande o suficiente pra cortar o início do áudio; em hardware forte o mesmo
trabalho termina rápido demais pra ser perceptível.

**2. Correção aplicada** (`app/templates/painel/tv.html`):
- **Ordem invertida:** `falarChamada(dados)` roda ANTES de
  `inserirChamada(dados)` no handler do evento `chamada` - o áudio começa a
  inicializar sem concorrência do reflow/repaint da animação, que passa a
  rodar só depois. `falarChamada()` não depende de nada que
  `inserirChamada()` atualiza (só usa `dados`), então a troca é segura.
- **`vozPreferidaCache`:** o resultado de `vozPtPreferida()` passa a ser
  calculado só quando `vozesCache` é atualizado (dentro de
  `atualizarVozesCache()` - load inicial, `voiceschanged`, poll de
  segurança), não mais em toda `falarChamada()`. A função `vozPtPreferida()`
  em si não mudou (continua pura, só lê `vozesCache`) - só passou a ser
  chamada com menos frequência.
- **`cancel()`+`speak()` continuam SÍNCRONOS**, um logo após o outro, sem
  nenhum `setTimeout` entre eles - ver item 2.1 abaixo (contradição
  encontrada e corrigida na revisão desta correção).

**2.1. Contradição encontrada e corrigida antes de fechar este adendo:** a
primeira versão desta correção também envolveu o `speak()` num
`setTimeout(() => speechSynthesis.speak(fala), 0)` logo após o `cancel()`,
como tentativa preventiva de baixo risco pro fator secundário (bug do
Chromium com `cancel()`+`speak()` no mesmo tick, item 1) - **isso desfazia
silenciosamente a correção da causa PRIMÁRIA**, sem os dois efeitos serem
percebidos como conflitantes numa primeira leitura textual. `setTimeout(fn,
0)` transforma a chamada de `speak()` numa **macrotask**: ela só roda depois
que TODO o código síncrono do handler termina - incluindo `inserirChamada()`,
que roda logo em seguida (o handler é síncrono, não usa `await`). Na prática,
`falarChamada()` só "rodava primeiro" até a linha do `cancel()`; o `speak()`
de fato só era chamado DEPOIS do reflow síncrono e da animação de
`box-shadow` de `inserirChamada()` - exatamente o cenário que a reordenação
do item 2 existe pra evitar, cancelando o ganho pretendido sem nenhum erro
visível (nem nos testes estruturais, que não checam ordem real de execução -
ver item 5). Corrigido revertendo `cancel()`+`speak()` pra síncronos: prioriza
a causa PRIMÁRIA confirmada pela investigação (item 1) em vez da mitigação do
fator secundário nunca confirmado neste caso - as duas mitigações, do jeito
que foram implementadas na primeira tentativa, eram mutuamente exclusivas na
prática, não aditivas.

**3. Instrumentação de timing, atrás de flag** (`?debug=1` na URL, não
`localStorage` - não deve sujar console em produção normal): mede com
`performance.now()` só o trabalho SÍNCRONO do handler (`falarChamada` +
`inserirChamada`), não quando o áudio de fato começa a soar (isso é
assíncrono/interno ao motor de voz, fora do alcance do JS). Uso:
`https://.../painel/tv/<slug>?debug=1`, ler o console do navegador na
própria máquina.

**4. PENDÊNCIA FÍSICA - mesmo padrão dos adendos de impressão (10.38/10.42):**
não há como medir o ganho real sem rodar na máquina Celeron reportada. Não
foi possível provar numericamente a melhora aqui (sem runtime de navegador
neste ambiente) - a confirmação final depende de reproduzir o problema
nessa máquina física, com e sem o parâmetro `?debug=1`, comparando os tempos
antes/depois desta correção e confirmando de ouvido se o corte de início
sumiu. Se o corte persistir mesmo com os números do `?debug=1` mostrando
melhora no tempo síncrono, a causa real estaria em outro lugar (ex.: dentro
do próprio motor de voz do SO/navegador, fora do alcance do JS) - próximo
passo de diagnóstico nesse cenário, fora do escopo desta correção.

**5. Testes:** suíte estrutural existente (`tests/test_regressao_painel_tv_
redesign.py`, `tests/test_regressao_audio_pregravado.py`,
`tests/test_regressao_som_configuravel.py`) já cobre a presença/isolamento
de `falarChamada`/`inserirChamada`/`vozPtPreferida` via checagem de string/
corpo de função no HTML renderizado - não depende da ORDEM de chamada dentro
do handler nem do valor cacheado, então continuou passando sem alteração
após a reordenação. Suíte completa (49 arquivos) sem falhas. Nenhum teste
novo dedicado (não dá pra medir performance real sem runtime de navegador -
mesma limitação já aceita em outros testes de JS do projeto).

**Arquivos alterados:** `app/templates/painel/tv.html`.

---

## 10.49. ADENDO — Corte de início da fala no painel de TV: causa DIFERENTE da 10.48 (atraso de warm-up do motor de TTS, não CPU)

> Reportado depois da 10.48 já aplicada: hardware TROCADO (máquina nova,
> mais potente), código já com a correção da 10.48 em produção, e o corte de
> início PERSISTIU. Dado novo do cliente: corta tanto em chamada por NOME
> ("Lucas Cardoso" saindo como "cas Cardoso" - perde "Lu") quanto em chamada
> só por CÓDIGO de senha (ex.: "BSG009") - sistêmico em toda `falarChamada()`,
> não específico de um fluxo. Como já não é mais CPU fraca disputando com
> reflow/animação (causa da 10.48, contida por hardware), a causa mais
> provável passa a ser um bug diferente: **atraso de "aquecimento" do próprio
> motor de `speechSynthesis`** - documentado no Chromium/SAPI Windows, onde
> os primeiros ~100-300ms de CADA utterance são engolidos porque o pipeline
> de áudio ainda não terminou de inicializar quando o navegador começa a
> tocar - independente de CPU, acontece em toda chamada, não só a primeira
> do dia. **Não confundir com a 10.48** (essa era CPU/reflow, específica de
> hardware fraco, já resolvida e contida) - este é um problema novo,
> encontrado depois, com mecanismo diferente.

**1. Investigação (código lido, sem runtime de navegador disponível neste
ambiente - mesma limitação já aceita em outros testes de JS do projeto):**

- **Texto exato da Utterance:** `montarTextoChamada(dados)` (`painel/tv.html`)
  substitui `{nome}`/`{senha}`/`{local}` no template configurável por
  empresa (`Empresa.texto_chamada_nome_template`/`texto_chamada_senha_
  template`, default `"{nome}, por favor, dirija-se a {local}."` e
  `"Atenção, senha {senha}. Por favor, dirija-se a {local}."`) - **sem
  nenhum prefixo/pausa embutido antes desta correção**. O texto que chegava
  em `fala.text` começava DIRETO na primeira sílaba do nome ou (dependendo
  do template configurado por cada empresa) potencialmente direto no
  código, sem nenhuma margem de segurança contra esse atraso de warm-up.
- **`fala.rate`/`fala.pitch`:** nenhum dos dois é definido em nenhum lugar
  do código - ambos ficam no default do navegador (`1.0`), sem
  customização que amplifique ou mitigue o corte.
- **`fala.volume`:** o único parâmetro customizado (`TV_VOLUME / 100`,
  cadastro por TV) - não interage com o atraso de warm-up, só com o volume
  final do áudio já iniciado.
- **`onstart`/`onboundary`:** nenhum dos dois eventos está em uso em
  nenhum lugar do arquivo - não existia nenhuma instrumentação capaz de
  medir quando o áudio de fato começa a soar vs. quando `speak()` foi
  chamado (limitação que continua existindo mesmo depois desta correção -
  ver item 4).

**2. Correção aplicada** (`app/templates/painel/tv.html`) - técnica de
"priming" (padrão documentado pra esse bug específico, escolhida a mais
simples primeiro, conforme pedido: prefixo de texto, não uma segunda
utterance separada):
- Nova constante `PRIMING_TTS_PREFIXO = ", "` (vírgula + espaço, não
  espaço puro - espaço no início costuma ser descartado pelo parser do
  motor sem gerar nenhum áudio de "sacrifício" real). **Vírgula, não
  reticências** (escolha revisada antes de fechar este adendo): entre os
  sinais de pontuação, vírgula é o marcador de pausa mais universal entre
  motores de TTS - praticamente todo motor trata "," como pausa breve,
  nunca soletra. Reticências ("...") têm histórico conhecido de serem
  verbalizadas por extenso ("reticências") em alguns motores PT-BR, o que
  anularia o efeito pretendido (viraria conteúdo audível real, não um
  "sacrifício" neutro) - risco desnecessário pra uma técnica cujo único
  objetivo é ser descartável. Espaço extra depois da vírgula não teria
  efeito garantido (motores tendem a colapsar espaços múltiplos na
  normalização de texto antes de sintetizar), por isso um só.
- Aplicado dentro de `falarChamada()`, no único ponto onde toda
  `SpeechSynthesisUtterance` é construída: `new SpeechSynthesisUtterance
  (PRIMING_TTS_PREFIXO + montarTextoChamada(dados))` - cobre nome E código
  automaticamente, sem duplicar lógica (mesma função já central pros dois
  fluxos, ver não-duplicação de áudio provada na 10.13).
- `montarTextoChamada()` continua só compondo o TEXTO da mensagem (nome/
  código + local) - o priming é uma preocupação de motor de TTS, mantida
  isolada em `falarChamada()`, não misturada na função de composição de
  texto (separação de camadas).
- **Não aplicado** ao caminho de áudio pré-gravado
  (`falarChamadaPregravada()`, `Tv.usa_audio_pregravado`) - esse fluxo
  concatena clipes `.wav` reais, não usa `speechSynthesis`, então não sofre
  desse bug de warm-up de motor.

**3. Alternativa mais robusta, NÃO implementada agora** (registrada pra
referência futura, caso o prefixo de texto não seja suficiente): disparar
uma `SpeechSynthesisUtterance` quase-vazia (ex.: um único espaço, volume
baixo) imediatamente ANTES da utterance real, só pra "acordar" o motor - a
real só seria chamada no evento `onend` da primeira (ou com um pequeno delay
medido). Mais complexa (duas utterances encadeadas, mais um ponto de falha
possível) - deliberadamente não aplicada nesta rodada, seguindo a orientação
de testar a técnica mais simples primeiro.

**4. PENDÊNCIA FÍSICA DUPLA - mesmo padrão dos adendos de impressão
(10.38/10.42) e da 10.48:** duas coisas distintas só a máquina real confirma,
nenhuma das duas verificável aqui (sem runtime de navegador neste ambiente):
1. **O corte de início realmente sumiu** - reproduzir numa máquina real
   (nome E código, algumas chamadas seguidas).
2. **A vírgula de priming não está sendo percebida/falada de forma audível**
   - ou seja, que a escolha do item 2 (vírgula em vez de reticências) de
   fato funciona como pausa neutra nos motores reais em uso (SAPI Windows/
   Chrome/Edge), sem ser lida como conteúdo ("vírgula") nem soar como um
   estalo/ruído perceptível antes de cada chamada.

Partimos da opção com menor risco conhecido entre as alternativas de
pontuação (item 2), mas "menor risco" não é "risco zero" - por isso ainda é
pendência, não confirmação. Se o corte persistir mesmo com o prefixo de
priming, ou se a vírgula for percebida como ruído/fala indesejada, os
próximos passos de diagnóstico seriam: (a) instrumentar `onstart`/
`onboundary` pra medir de verdade o atraso entre `speak()` e o início real
do áudio (item 1 confirma que essa instrumentação não existe hoje); (b)
partir pra a alternativa mais robusta do item 3 (utterance de aquecimento
separada, que sacrifica uma utterance inteira em vez de um único caractere,
com controle maior sobre o que exatamente é ouvido).

**5. Testes:** novo arquivo `tests/test_regressao_priming_tts.py` (4 casos,
estrutural, mesmo padrão dos outros testes de `tv.html` - confirma presença/
isolamento da técnica no código renderizado, não prova numericamente que o
corte sumiu, mesma limitação de sempre sem runtime de navegador): constante
`PRIMING_TTS_PREFIXO` presente com o valor esperado; `falarChamada()` aplica
o prefixo na `Utterance`, cobrindo nome e código pelo mesmo ponto;
`montarTextoChamada()` não conhece o priming (responsabilidade isolada);
`falarChamadaPregravada()` (fallback `.wav`) não usa o priming. Suíte
completa (50 arquivos) sem falhas.

**Arquivos alterados:** `app/templates/painel/tv.html`; novo
`tests/test_regressao_priming_tts.py`.

---

## 10.50. ADENDO — EXPERIMENTAL: heartbeat periódico do motor de speechSynthesis (corte de início após ociosidade, causa diferente da 10.48/10.49)

> Terceiro problema relacionado a corte de início de fala no painel de TV,
> com causa e correção distintas das duas anteriores. **Não confundir com a
> 10.48** (CPU fraca disputando com reflow/animação, específica de hardware,
> já contida) **nem com a 10.49** (perda sistemática da primeira sílaba em
> uso contínuo, corrigida com prefixo de priming). Dado de campo confirmado
> com o cliente: o corte de início volta a acontecer toda vez que a TV fica
> um tempo sem chamar nenhuma senha (não é só 1x por dia/sessão) - a
> PRIMEIRA chamada depois de um período ocioso corta, a segunda em diante
> (enquanto continuar chamando) sai normal.

**1. Base do raciocínio (não verificável empiricamente neste ambiente, sem
runtime de navegador - documentado como fundamentação, não como prova):** o
padrão "só a 1ª chamada após ociosidade corta, as seguintes saem normais" é
consistente com a literatura conhecida sobre o Web Speech API - o
engine/pipeline de síntese de alguns navegadores (Chromium/SAPI Windows)
pode ser desalocado ou suspenso pelo próprio SO/navegador depois de um
período sem uso (economia de recursos), precisando reinicializar no próximo
`speak()` - a mesma reinicialização que, na 10.49, mostrou engolir os
primeiros ~100-300ms de uma utterance. A 10.49 tratou o sintoma "toda
chamada corta um pouco" (motor sempre frio no início de cada fala); este é
um sintoma relacionado mas mais específico: "motor esfria de verdade depois
de ficar OCIOSO por um tempo maior, e demora mais pra 'esquentar' de volta
na primeira chamada seguinte" - daí a proposta de mantê-lo aquecido de
propósito com um heartbeat, em vez de só amortecer o corte com um prefixo.

**2. Por que NÃO é o workaround da 10.13 reintroduzido** (revisado com
cuidado antes de implementar, por pedido explícito - não reintroduzir sem
aprovação): a nota daquele adendo registra um keep-alive especulativo de
`speechSynthesis.pause()/resume()`, testado e **revertido** por não ser
verificável no ambiente de teste e ter relatos de TAMBÉM causar cortes em
vez de só preveni-los. A razão do risco: `pause()`/`resume()` manipulam o
estado de síntese AMBIENTE do motor inteiro - pausam/retomam o que quer que
esteja tocando OU PRESTES A TOCAR, real ou não. Se uma chamada real
começasse a falar bem no instante de um `pause()` especulativo, o `resume()`
seguinte não tinha garantia de retomar do jeito certo (bug conhecido do
Chrome) - o keep-alive podia cortar uma fala que não teria cortado sozinha.
**Esta correção é estruturalmente diferente:** o heartbeat só chama
`speak()` de uma utterance PRÓPRIA, curtíssima e quase inaudível - nunca
manipula o que já está em andamento (nem `pause()`, nem `resume()`, nem
`cancel()` de propósito). Se uma chamada real chegar durante um heartbeat,
o `cancel()` que `falarChamada()` já faz - mecanismo existente desde antes
desta correção, "evita empilhar falas se várias chamadas chegarem rápido" -
cancela o heartbeat exatamente como cancelaria qualquer fala anterior; não é
um caminho de interação novo nem um estado adicional pra gerenciar, é o
mesmo cancelamento que já protege contra chamadas reais em sequência
rápida.

**3. Correção aplicada** (`app/templates/painel/tv.html`), **EXPERIMENTAL**
- mesmo status inicial do workaround da 10.13, que precisou ser revertido:
- `HEARTBEAT_TTS_INTERVALO_MS = 12000` (12s, dentro da faixa 10-15s
  sugerida) - `setInterval(dispararHeartbeatTts, HEARTBEAT_TTS_INTERVALO_MS)`
  dentro do mesmo bloco `if ("speechSynthesis" in window)` da configuração
  de voz existente.
- `dispararHeartbeatTts()`: checagem de `speechSynthesis.speaking`
  OBRIGATÓRIA antes de qualquer disparo (não um timer solto) - nunca deve
  tocar por cima de uma fala real (ou de outro heartbeat) em andamento.
  Guardada pelos MESMOS gates de `falarChamada()` (`TV_SOM_ATIVO`,
  `TV_USA_AUDIO_PREGRAVADO`, suporte a `speechSynthesis`) - sem som ligado
  ou nesta TV usando o fallback de áudio pré-gravado, manter o motor
  "aquecido" não serve pra nada.
- `HEARTBEAT_TTS_VOLUME = 0.02` (baixo, mas **não zero** - decisão
  documentada no código: volume 0 evitaria ruído perceptível, mas corre o
  risco de o motor otimizar o caminho e nunca de fato exercitar o pipeline
  de áudio real, o que anularia o propósito do heartbeat).
- `HEARTBEAT_TTS_TEXTO = "a"` (uma vogal curta, não pontuação nem espaço
  puro) - objetivo OPOSTO ao `PRIMING_TTS_PREFIXO` da 10.49 (que quer ser
  descartável): aqui o motor precisa genuinamente sintetizar e tocar um
  fonema real, não só processar um caractere de pausa que algum motor
  poderia resolver como silêncio sem exercitar a saída de áudio de verdade.
- Log opcional atrás do `?debug=1` já existente (10.48/10.49): cada disparo
  de heartbeat loga no console quando `DEBUG_TIMING` estiver ativo, pra dar
  pra ver na máquina real se está disparando quando esperado (e com que
  frequência de fato, já que `setInterval` não garante precisão sob carga).

**4. PENDÊNCIA FÍSICA + STATUS EXPERIMENTAL - mesmo padrão dos adendos de
impressão (10.38/10.42) e da 10.48/10.49, com uma ressalva a mais:** esta
correção pode precisar ser **revertida**, assim como o workaround da 10.13,
se o teste físico mostrar que o heartbeat interfere negativamente (áudio de
fundo audível demais mesmo em volume baixo, engasgo perceptível quando uma
chamada real coincide com um heartbeat, ou qualquer efeito colateral não
previsto). Não é uma promessa de que vai funcionar - é uma tentativa
fundamentada, estruturalmente diferente do workaround já revertido, mas
ainda não validada em hardware real. Confirmação pendente: (1) o corte após
ociosidade sumiu; (2) o heartbeat em si não é perceptível/incômodo; (3) uma
chamada real que coincida com um heartbeat em andamento sai limpa (sem
engasgo, sem corte adicional).

**5. Testes:** novo arquivo `tests/test_regressao_heartbeat_tts.py` (10
casos, estrutural, mesma limitação de sempre sem runtime de navegador -
confirma presença/config da técnica, não prova que o corte sumiu nem que o
heartbeat é inaudível): constantes de intervalo/volume/texto com os valores
decididos; `dispararHeartbeatTts()` checa `speechSynthesis.speaking` ANTES
de chamar `speak()`; usa as constantes certas; respeita os mesmos gates de
`falarChamada()`; `setInterval` liga a função no intervalo configurado; log
de debug presente; comportamento em runtime (via `TV_SOM_ATIVO`/
`TV_USA_AUDIO_PREGRAVADO`) confirmado para TV com som desligado e TV com
áudio pré-gravado. Suíte completa (51 arquivos) sem falhas.

**Arquivos alterados:** `app/templates/painel/tv.html`; novo
`tests/test_regressao_heartbeat_tts.py`.

---

## 10.51. ADENDO — EXPERIMENTAL: "despertar" o hardware de saída de áudio via Web Audio API (quarta tentativa, causa diferente das anteriores)

> Quarta tentativa pra corte de início no painel de TV, causa diferente das
> três anteriores: enquanto 10.48 (CPU/reflow), 10.49 (warm-up do motor de
> TTS a cada fala) e 10.50 (motor de TTS esfriando após ociosidade) atacam o
> pipeline de `speechSynthesis`, esta ataca o **hardware/driver de saída de
> áudio em si**, via um pipeline separado (Web Audio API). Convive com a
> 10.50 - não a substitui, os dois continuam ativos.

**1. Mecanismo:** `despertarSaidaAudio(callback)` (`app/templates/painel/
tv.html`) cria (ou reaproveita) um `AudioContext`, garante `resume()` se o
estado estiver `'suspended'`, toca um tom curto via `OscillatorNode` +
`GainNode` (`DESPERTAR_AUDIO_DURACAO_MS = 130`, dentro de 120-150ms;
`DESPERTAR_AUDIO_FREQUENCIA_HZ = 150`; `DESPERTAR_AUDIO_VOLUME = 0.02` -
mesmo valor e mesma justificativa do `HEARTBEAT_TTS_VOLUME` da 10.50: baixo
o bastante pra não incomodar, alto o bastante pra exercitar a saída de
áudio de verdade). `callback()` só roda no evento `oscillator.onended`
REAL, não um `setTimeout` estimado - a fala real só começa depois que o tom
efetivamente terminou.

**2. Integração:** `socket.on("chamada", ...)` troca `falarChamada(dados)`
direto por `despertarSaidaAudio(() => falarChamada(dados))`.
`inserirChamada()` mantém a posição já estabelecida na 10.48 (depois de
`falarChamada()`, dentro do mesmo callback).

**3. Fallback gracioso:** sem `AudioContext`/`webkitAudioContext`
disponível, ou qualquer erro na criação/uso do contexto (`try`/`catch`),
cai direto pra `callback()` - `falarChamada()` roda normalmente, sem o tom.

**3.1. Guard de dupla-chamada (revisão pós-review, mesmo padrão de `feito`
em `falarChamadaPregravada()`):** existem vários pontos de saída possíveis
(sem Web Audio API, `catch` de `resume()` rejeitado, `catch` externo de
qualquer outra falha, e o `onended` real do oscilador) - numa janela
estreita (algo lançar exceção DEPOIS de `oscilador.start()` já ter sido
chamado com sucesso, ex.: na própria linha `oscilador.stop()` logo abaixo),
tanto o `catch` quanto o `onended` poderiam chamar `callback()` pro MESMO
evento, disparando `falarChamada()` duas vezes (fala duplicada, não
silêncio - mas ainda um bug real, identificado em revisão antes do
commit). `chamarUmaVez()` (variável `chamado`, guard local à função) garante
uma única execução de `callback()` não importa qual caminho dispare
primeiro - `callback()` em si só é invocado de dentro dela, nunca
diretamente em nenhum dos pontos de saída.

**4. Log atrás de `?debug=1`** (já existente desde a 10.48): dispara/termina
do tom.

**5. EXPERIMENTAL - pendência física, mesmo padrão dos adendos anteriores
desta série:** nenhuma prova de que resolve, nem de que o tom não é
perceptível. Se interferir negativamente, pode precisar ser revertido.

**6. Testes:** novo `tests/test_regressao_despertar_audio.py` (6 casos,
estrutural, mínimo por pedido explícito - presença da função, encadeamento
via `onended`, fallback gracioso em todos os caminhos de falha, guard de
dupla-chamada, heartbeat da 10.50 intocado). Suíte completa (52 arquivos)
sem falhas.

**Arquivos alterados:** `app/templates/painel/tv.html`; novo
`tests/test_regressao_despertar_audio.py`.

---

## 10.52. ADENDO — Destaque visual por tempo de espera (escala de 3 níveis) na tabela "Senhas em espera"

> Pedido: destaque visual na tabela "Senhas em espera" quando uma senha
> ultrapassa 70% (atenção) ou 100% (estourado) do `Fila.tempo_espera_maximo_
> min`. Escopo explícito, com precedente citado: precisa aparecer em
> `painel_fila.html` E `painel_local.html` - a mesma tabela existe nas duas
> telas, sem `{% include %}` compartilhado entre elas, e uma feature dessa
> tabela (o botão "Chamar" por linha) já tinha ficado só numa das duas antes
> de ser corrigido pra existir nas duas (ver `tests/test_regressao_botao_
> chamar_por_linha.py`).

**1. Investigação confirmada antes de implementar:** `painel_fila.html` e
`painel_local.html` são templates SEPARADOS - nenhum `{% include %}` cobre a
tabela "Senhas em espera" nos dois de uma vez (só `_redirecionar_busca.html`
é compartilhado, e é outro bloco da tela). O que os dois compartilham é o
`<style>` de `atendimento/_layout.html` (`{% extends %}` em ambos) - CSS novo
lá cobre as duas telas de graça, mas a lógica JS (cálculo do nível a cada
segundo) precisa ser implementada nos DOIS arquivos explicitamente, o que foi
feito. `Fila.tempo_espera_maximo_min` já chegava ao contexto de render das
duas telas (`fila`/`s.fila` já eram objetos `Fila` completos em ambas) - só
não estava sendo lido ainda.

**2. Escala de 3 níveis, calculada no cliente** (mesmo cronômetro "T. Espera"
que já roda a cada segundo, `atualizarRelogios()`): `classeDestaqueEspera
(segundosEspera, tempoMaximoMin)` retorna `null` (normal), `"espera-atencao"`
(≥70% do limite) ou `"espera-estourada"` (≥100%). Fallback explícito: fila
sem `tempo_espera_maximo_min` configurado (`NULL`) nunca recebe destaque
nenhum, não importa quanto tempo a senha espere.

**3. CSS compartilhado** (`atendimento/_layout.html`): borda esquerda
discreta (`.espera-atencao`/`.espera-estourada`, mesmo princípio já usado no
indicador de prioridade da lista do painel de TV, seção 10.4 - "borda, não
o preenchimento inteiro", menor ruído visual) + fundo mais forte só no nível
"estourado" (o que exige ação imediata).

**4. `painel_fila.html` (uma fila só na tela):** `TEMPO_ESPERA_MAXIMO_MIN` é
uma constante JS ÚNICA (`{{ fila.tempo_espera_maximo_min | tojson }}`) -
todas as linhas são da mesma fila, sem necessidade de repetir o valor por
linha.

**5. `painel_local.html` (rodízio agrega N filas, cada uma com seu próprio
limite):** o valor vai POR LINHA, não uma constante global - `data-tempo-
maximo-min` na `<tr>`, tanto no render inicial (Jinja, `s.fila.tempo_espera_
maximo_min`) quanto na regeneração via WebSocket (`linhaEspera(s)`, JS).
`_serializar_espera_local` (`app/atendimento/chamada.py`) ganhou o campo
`tempo_espera_maximo_min` no JSON de `dados_local` - sem isso, o destaque
sumiria a cada refresh em tempo real (evento `fila_atualizada`).

**6. Recálculo em tempo real:** a tabela já atualiza por dois caminhos - o
cronômetro "T. Espera" via `setInterval(atualizarRelogios, 1000)` (a cada
segundo) e o conteúdo da tabela via WebSocket (`fila_atualizada` →
`atualizarDados()` → `renderizarDados()` → chama `atualizarRelogios()` no
final). O destaque foi encaixado dentro do próprio `atualizarRelogios()` (o
mesmo laço que já calcula o tempo decorrido de cada `.t-espera`) - nenhum
timer novo, reaproveita o que já rodava a cada segundo.

**7. Testes:** novo `tests/test_regressao_destaque_tempo_espera.py` (7
casos) - cobre EXPLICITAMENTE os dois templates (não só "a feature
funciona"): CSS/função presentes nas duas telas; constante única em
`painel_fila.html` (com e sem limite configurado); atributo por linha em
`painel_local.html` com um Local cujo rodízio agrega uma fila COM limite e
uma SEM limite (prova que cada linha usa o limite da SUA PRÓPRIA fila, não
uma constante errada pra uma delas); JSON de `dados_local` carregando o
campo por item. Limitação de sempre: sem runtime de navegador, o cálculo em
si (`classeDestaqueEspera`) não é executado nos testes - só confirma que os
dados certos chegam ao HTML/JSON, no lugar certo. Suíte completa (54
arquivos) sem falhas.

**Arquivos alterados:** `app/templates/atendimento/_layout.html` (CSS
compartilhado), `app/templates/atendimento/painel_fila.html`, `app/
templates/atendimento/painel_local.html`, `app/atendimento/chamada.py`
(`_serializar_espera_local`); novo `tests/test_regressao_destaque_tempo_
espera.py`.

> **Revisão (mesma sessão, pedido do cliente com referência visual):** o
> ALVO do destaque mudou. A primeira versão aplicava a classe na `<tr>`
> inteira (`tr.espera-atencao td:first-child` - borda esquerda só na coluna
> "Senha" - e `tr.espera-estourada` - fundo na linha inteira) - dois alvos
> inconsistentes entre si, nenhum no lugar pedido. Corrigido pra um badge/
> marca-texto aplicado só no elemento `.t-espera` (o valor do cronômetro em
> si, ex. "00:18:27"), com o mesmo tratamento visual pros dois níveis
> (padding, cantos arredondados), só a cor mudando - reaproveitando os
> mesmos tokens de cor já usados em `.flash.aviso` (atenção) e `.flash.
> error`/`.badge.prioridade` (estourado). A escala 70%/100% e o cálculo em
> `classeDestaqueEspera()` **não mudaram** - só onde a classe resultante é
> aplicada (`el.classList` no próprio `.t-espera`, não mais `linha.
> classList` na `<tr>`). Estrutura HTML já tinha o valor do tempo isolado
> num `<span class="t-espera">` próprio (separado do horário pequeno "·
> 12:24", que fica num `<span class="hora-relogio">` distinto) nos dois
> templates - não precisou de nenhum elemento novo, só trocar o alvo do
> `classList`. Testes atualizados pra checar o seletor novo (`.t-espera.
> espera-*`) E confirmar que o antigo (`tr.espera-*`) foi removido, não só
> deixado ao lado.

---

## 10.53. ADENDO — Dois bugs independentes na tela "Editar local": checklist de filas fora de escopo + barra de contexto sem efeito em telas de editar

> Dois problemas relatados juntos na tela "Editar local" (admin), com causas
> CONFIRMADAS independentes (investigado antes de corrigir - não presumido):
> (1) o checklist "Filas de destino permitidas" mostrava filas de TODAS as
> unidades da empresa, não só da unidade do Local; (2) o seletor "Empresa" no
> topo não trocava os dados ao mudar de empresa. A correção do item 2, ao ser
> generalizada, revelou o MESMO padrão repetido em outras 9 rotas de editar
> objeto do admin - corrigidas todas juntas nesta sessão (mesmo princípio já
> visto antes: o botão "Chamar" faltando num template irmão, os 3 links
> apontando pro destino errado - um achado pontual vira varredura completa).

**1. Checklist "Filas de destino permitidas" sem filtro de unidade (bug de
EXIBIÇÃO, não de integridade):** `filas_da_empresa()` (`app/admin/helpers.py`)
usada em `novo_local()`/`editar_local()` traz TODA a empresa, sem
`unidade_id` - diferente de `rodizio_local()` (mesmo arquivo), que já
filtrava corretamente (`Fila.query.filter_by(..., unidade_id=local.unidade_id)`),
com um comentário próprio admitindo essa diferença desde que foi escrito. A
regra "mesma unidade" **já era** validada no submit (`_validar_local`) -
nenhuma seleção cruzada era de fato salva, só EXIBIDA como opção possível.

Como o campo "Unidade" é um `<select>` dentro do MESMO formulário (pode
mudar antes de salvar, sem reload), a correção não filtra a query do
servidor (que continua trazendo a empresa inteira, de propósito) - filtra em
tempo real no cliente: cada item do checklist (`admin/locais/form.html`)
ganhou `data-unidade-id="{{ fila.unidade_id }}"`, e um `filtrarFilasPorUnidade()`
esconde (`hidden`, nunca remove do DOM) os itens que não batem com o
`<select id="unidade_id">` atual, tanto no load quanto a cada `change`.
Checkbox marcado que sai de vista é desmarcado também - não deixa uma
seleção escondida sobreviver silenciosamente até o submit (mesmo que o
backend já rejeitasse, a UX de "marquei algo que sumiu mas continua
contando" é confusa). Mesmo template (`admin/locais/form.html`) serve
`novo_local()` e `editar_local()` - a correção cobre as duas rotas de graça.

**2. Barra de contexto (Empresa/Unidade) sem efeito em telas de editar um
objeto - levantamento COMPLETO, não só Local:** confirmado que
`editar_local()` deriva `empresa` sempre de `local.empresa_id`, nunca da
querystring/sessão da barra de contexto - **comportamento intencional**,
documentado desde antes em `app/admin/__init__.py::unidades_do_contexto`
("nunca de um valor de sessão à parte, o que poderia divergir do que a
página está realmente mostrando"). O bug real não é a rota ignorar o
parâmetro (isso está certo) - é o seletor continuar interativo e visível,
sem indicar que a troca não tem efeito nenhum ali.

Levantamento de TODAS as rotas do admin com esse padrão (deriva a empresa
de um objeto específico, `objeto.empresa_id`/`objeto.empresa`, ignora o
contexto do topo) - 10 no total, todas corrigidas:

| Rota | Arquivo |
|---|---|
| `editar_empresa` | `app/admin/empresas.py` |
| `editar_fila` | `app/admin/filas.py` |
| `editar_local` | `app/admin/locais.py` |
| `rodizio_local` | `app/admin/locais.py` |
| `editar_tipo_senha` | `app/admin/tipos_senha.py` |
| `editar_totem_botao` | `app/admin/totem_botoes.py` |
| `editar_totem` | `app/admin/totens.py` |
| `editar_tv` | `app/admin/tvs.py` |
| `editar_unidade` | `app/admin/unidades.py` |
| `editar_usuario` | `app/admin/usuarios.py` |

Decisão do cliente: **esconder** o seletor nessas telas (não desabilitar com
aviso). Nova flag de template `esconder_seletor_contexto` - cada uma das 10
rotas acima passa `esconder_seletor_contexto=True` em TODOS os
`render_template()` daquela função (inclusive nos caminhos de erro que
re-renderizam o form, não só o GET inicial). `admin/_layout.html` mudou a
condição da barra de `{% if empresa %}` pra `{% if empresa and not
esconder_seletor_contexto %}` - default (sem passar a flag) continua
mostrando a barra normalmente em todas as telas de LISTAGEM, sem nenhuma
mudança de comportamento nelas.

**Rotas fora do escopo, confirmadas de propósito:** `novo_local`/demais
rotas `novo_*` (chamam `resolver_empresa_contexto()` de verdade, o seletor
funciona ali - contexto legítimo pra escolher em qual empresa criar);
`listar_locais`/ocupacao de locais/demais listagens (idem); `_editar_usuario_
global` (super admin sem empresa - `empresa` já é `None`, barra não aparece
mesmo sem a flag).

**3. Testes:** novo `tests/test_regressao_admin_editar_contexto.py` (14
casos) - item 1: `data-unidade-id` presente pras filas das duas unidades e o
JS de filtro no HTML renderizado, confirmado em `novo_local` E
`editar_local` (mesmo template); item 2: as 10 rotas da tabela acima, uma
por uma, confirmando ausência da barra de contexto - mais um caso de
controle (`listar_locais`) provando que a barra continua aparecendo
normalmente numa listagem comum, não foi escondida globalmente. Suíte
completa (55 arquivos) sem falhas.

**Arquivos alterados:** `app/templates/admin/_layout.html` (flag
`esconder_seletor_contexto`), `app/templates/admin/locais/form.html`
(filtro por unidade do checklist); `app/admin/empresas.py`, `app/admin/
filas.py`, `app/admin/locais.py`, `app/admin/tipos_senha.py`, `app/admin/
totem_botoes.py`, `app/admin/totens.py`, `app/admin/tvs.py`, `app/admin/
unidades.py`, `app/admin/usuarios.py` (flag em todos os `render_template`
das 10 rotas); novo `tests/test_regressao_admin_editar_contexto.py`.

> **Regression + varredura completa (mesma investigação, achada em teste
> real pós-deploy):** o filtro por unidade do item 1 acima não funcionava de
> verdade em produção - o atributo `hidden` era setado certinho pelo JS
> (`item.hidden = !mesmaUnidade`), mas o item continuava visível. Causa raiz:
> `[hidden] { display: none; }` é uma declaração de origem **UA** (folha de
> estilo padrão do navegador) - QUALQUER `display:` de origem **AUTOR** que
> combine com o elemento sempre vence uma declaração UA normal na cascata,
> **independente da especificidade relativa entre os dois seletores**.
> `.checkboxes label { display: flex; ... }` (`admin/_layout.html`) tem
> exatamente esse conflito, sem nenhuma regra `[hidden]` própria pra vencer.
>
> **Este é o MESMO padrão já corrigido DUAS vezes antes** neste projeto -
> `atendimento/_layout.html::.aviso-encerramento` (seção 10.18) e
> `painel/tv.html::.chamada-badge-prioritario` (redesign do painel de TV) -
> cada uma com sua própria regra `[seletor][hidden] { display: none; }`. Por
> ser a 3ª ocorrência do mesmo padrão, virou varredura completa (mesmo
> princípio já aplicado várias vezes nesta sessão: achado pontual não fica
> isolado, procura-se o padrão inteiro) em vez de corrigir só o caso
> reportado:
>
> | Elemento | Seletor que o atinge | `display:` de autor? | Status antes da varredura |
> |---|---|---|---|
> | `.checkboxes label` (admin/locais/form.html) | `.checkboxes label` | `display: flex` | **quebrado** |
> | `.selecionado-redirecionar` (atendimento/_redirecionar_busca.html) | `.selecionado-redirecionar` | `display: flex` | **quebrado (achado novo)** |
> | `.aviso-encerramento` (atendimento/_layout.html) | `.aviso-encerramento` | `display: flex` | já corrigido (seção 10.18) |
> | `.chamada-badge-prioritario` (painel/tv.html) | `.chamada-badge-prioritario` | `display: inline-flex` | já corrigido |
> | `#vazio-filas-turno`/`.vazio`, `#erro-turno`/`.flash.error`, `#aviso-bloqueio`/`.flash.aviso` (×2), `#dropdown-redirecionar`/`.dropdown-redirecionar`, `#busca-redirecionar`/`.busca-redirecionar` | — | nenhum `display:` de autor | seguro (só `display` de UA no elemento, `[hidden]` UA já vence por especificidade dentro da mesma origem) |
>
> **Segundo caso quebrado, achado só nesta varredura:**
> `.selecionado-redirecionar` (o balão "→ Redirecionando para: X" do widget
> de busca, `atendimento/_redirecionar_busca.html`, incluído tanto por
> `painel_fila.html` quanto `painel_local.html` - corrige as duas telas de
> uma vez) tinha o mesmo problema, nunca reportado por não ter sido
> percebido visualmente ainda.
>
> **Correção:** regra própria em cada um dos dois arquivos, mesma técnica já
> validada nos dois casos anteriores - `.checkboxes label[hidden] {
> display: none; }` (`admin/_layout.html`) e `.selecionado-redirecionar
> [hidden] { display: none; }` (`atendimento/_redirecionar_busca.html`).
> Nenhuma mudança no JS (`filtrarFilasPorUnidade()` já estava correto - o
> bug era 100% CSS, sem nenhum erro visível no console, exatamente o motivo
> de valer a pena a varredura em vez de confiar em "parece que devia
> funcionar").
>
> **Testes:** novo `tests/test_regressao_hidden_display_sweep.py` (3 casos)
> - confirma a regra `[hidden] { display: none; }` própria presente pra cada
> seletor corrigido, extraindo o corpo exato da regra (mesmo padrão já usado
> em `test_regressao_encerramento_automatico.py`/`test_regressao_painel_tv_
> redesign.py` pros dois casos anteriores) - inclui chamar uma senha de
> verdade em `painel_local` antes de checar (o widget só é incluído ali
> quando há `senha_atual`, diferente de `painel_fila`). Suíte completa (56
> arquivos) sem falhas.

---

## 10.54. ADENDO — Filtro De/Até dos relatórios: calendário nativo + padrão "hoje" em vez de 30 dias

Pedido do cliente com print de referência (tela "Tempo médio de espera"): os
campos "De"/"Até" eram `<input type="text">` (placeholder "AAAA-MM-DD"), sem
nenhum calendário nativo do navegador, e o padrão de "sem filtro na URL" era
uma janela dos últimos 30 dias.

**Investigação:** as 4 telas de relatório que têm esse filtro
(`tempo_espera.html`, `tempo_atendimento.html`, `volume.html`, `ranking.html`)
já compartilham um único partial (`relatorios/_macros.html::form_filtros`) e
um único resolvedor de filtros (`relatorios/helpers.py::resolver_filtros`) -
não havia duplicação por tela para corrigir. `relatorios/dashboard.html`
(dashboard operacional) não tem esse filtro (é uma visão "agora", sem
período) - fora de escopo. Nenhum outro relatório fora de `relatorios/` usa
esse mesmo padrão De/Até.

Os campos já usavam formato ISO `AAAA-MM-DD` no valor, exatamente o formato
que `<input type="date">` espera - trocar `type="text"` por `type="date"` foi
suficiente pra ganhar o calendário nativo sem nenhuma mudança de parsing no
back-end (`resolver_filtros` já fazia `datetime.strptime(..., "%Y-%m-%d")`) e
sem biblioteca nova, alinhado ao restante do admin.

**Correção (2 arquivos):**
- `relatorios/_macros.html`: `type="text"` → `type="date"` nos dois campos
  (`data_inicio`, `data_fim`); removido o `placeholder="AAAA-MM-DD"` (sem
  função em um `<input type="date">`, o navegador já mostra o formato local).
- `relatorios/_layout.html`: seletor CSS `.filtros input[type="text"]`
  ampliado para também cobrir `input[type="date"]` (mesmo estilo de borda/
  padding, senão os campos ficariam sem a formatação já aplicada aos demais).
- `relatorios/helpers.py::resolver_filtros`: `hoje = date.today()` trocado
  por `hoje = agora_na_clinica().date()` (fuso da clínica, seção 10.37 - não
  o fuso do servidor); o padrão de `data_inicio_str`/`data_fim_str` quando
  ausentes na querystring passou de `hoje - timedelta(days=30)` para `hoje`
  em ambos os campos (De = Até = hoje).

> **Correção adicional (revisão antes da aprovação) — mesmo bug de 3h da
> 10.39, reaberto neste call-site:** a primeira versão desta correção montava
> `data_inicio`/`data_fim_exclusivo` com `datetime.combine(hoje, ...)` -
> **naive, sem nenhuma conversão de fuso** - comparado direto contra
> `Senha.criada_em` (gravado em UTC naive, seção 8). `hoje`/as datas De/Até
> digitadas pelo usuário são dias CIVIS da clínica, não dias em UTC; sem
> converter, uma senha criada entre 21h e 23:59 de Brasília (já madrugada do
> dia seguinte em UTC) ficaria de fora do filtro "hoje" - exatamente o mesmo
> bug que `janela_dia_clinica_em_utc()` (10.39/10.44) foi criada para evitar,
> só que aqui reaberto num call-site novo. Corrigido reaproveitando essa
> mesma função: `data_inicio, _ = janela_dia_clinica_em_utc(datetime.combine(
> data_inicio_data, datetime.min.time(), tzinfo=fuso))` e o mesmo padrão
> (pegando o segundo elemento da tupla) para `data_fim_exclusivo` a partir de
> `data_fim_data` - vale tanto pro padrão "hoje" quanto pra qualquer filtro
> explícito De/Até digitado pelo usuário (o range inteiro estava exposto, não
> só o default).

**Testes:** novo `tests/test_regressao_relatorios_filtro_data.py` (7 casos),
com o relógio do servidor SIMULADO (mesmo padrão de `test_regressao_fuso_
horario.py`, patch de `app.utils.tempo._instante_utc`) às 22h de Brasília -
dentro da janela onde o bug de 3h se manifestaria se a conversão de fuso não
estivesse correta. Confirma `type="date"` presente nas 4 telas; confirma que
uma senha criada às 22h de Brasília (madrugada em UTC) aparece no filtro
"hoje" sem filtro explícito, e que os campos De/Até vêm preenchidos com o dia
CIVIL da clínica (não o dia em UTC); confirma que um filtro explícito
continua funcionando como antes. Suíte completa sem falhas.

**Oportunidades de melhoria notadas, NÃO implementadas (fora do pedido):**
- `filtros_para_querystring` (usado pelos links de exportar CSV/Excel)
  reconstrói o filtro a partir de `filtros.data_inicio_str`/`data_fim_str` -
  com o padrão agora sendo "hoje" em vez de 30 dias, os links de export sem
  filtro explícito exportam só o dia atual (antes exportavam 30 dias) sim,
  isso já é o comportamento pedido (consistência De/Até em toda a tela,
  export incluso) - citado aqui só para registro, não é um bug.
- Os 4 templates repetem a mesma estrutura de tabela/exportação quase
  idêntica (cabeçalho, `{% if not linhas %}`, link CSV/Excel) - um partial
  compartilhado reduziria repetição, mas é refactor sem urgência, não pedido.

---

## 10.55. ADENDO — Consolidação pré-dark-mode: variáveis semânticas novas + `<nav>` unificado (etapa 1 de 2)

> Pedido do cliente: dark mode + claro + automático em TODO o projeto (admin,
> atendimento, totem, painel de TV, relatórios, login), com botão de alternar
> visível em TODAS as telas, inclusive totem/TV — **decisão CONSCIENTE do
> cliente apesar do risco levantado**: totem/TV são telas públicas/kiosk sem
> "dono" — qualquer pessoa pode tocar o botão; a preferência fica salva por
> **navegador** via `localStorage`, não por conta/pessoa, então um totem pode
> ficar no tema errado até alguém notar e corrigir manualmente. **Risco
> aceito explicitamente pelo cliente, registrado aqui como tal** — não é uma
> lacuna descoberta depois, é uma escolha consciente feita antes de
> implementar.
>
> Esta seção documenta só a **etapa 1 de 2**: levantamento completo (FASE 1,
> números reais via grep, sem código) seguido de consolidação MECÂNICA da
> base CSS (troca de valor, sem tema escuro nenhum ainda) — pré-requisito
> pra etapa 2 (dark mode em si) não precisar caçar 141 pontos individuais
> espalhados pelo projeto.

**Levantamento (FASE 1, antes de qualquer código):**
- `--gf-*` (10 variáveis) definidas em UM lugar só (`gestfila.css`, `:root`)
  — nenhuma duplicação entre áreas, todas as 6 áreas (admin/atendimento/
  totem/painel/relatórios/auth) já carregam o mesmo arquivo via `<link>`.
- **151 ocorrências de hex + 23 de `rgba()`** fora de variável, espalhadas
  por 20 arquivos de template — das quais 10 hex eram a própria definição
  em `gestfila.css` (fonte, não risco). As 141 restantes se resumiam a **~20
  valores distintos reutilizados** (não caos disperso): `#fff`/`#ffffff`
  (46×, fundos de card), `#555`/`#666`/`#999`/`#888`/`#aaa`/`#777` (38×,
  texto secundário, nunca nomeado), família cinza-clara `#eceef2`/`#e5e8ea`/
  `#f0f1f3`/`#f7f8fa`/`#f1f3f6`/`#e8eaec`/`#d0d4dc` (39×, bordas/fundos
  sutis), `#eaf2f8`/`#fdf0e2`/`#e3f6e8` (fundos pastel de status
  info/alerta/sucesso).
- `<nav>` (admin/atendimento/relatórios/`core/dashboard.html`) era CSS+HTML
  quase byte-a-byte idêntico, duplicado 4×, nunca centralizado — admin tinha
  pequenas diferenças reais (mais links, precisa de quebra de linha), as
  outras 3 eram idênticas.
- CSS de impressão térmica (`totem/senha_gerada.html`, único `@media print`
  do projeto) já é 100% isolado — preto/branco fixos, nenhuma `var(--gf-*)`
  dentro do bloco de impressão — confirmado por leitura direta, não vaza
  dark mode pro cupom físico.
- Estrutura de cabeçalho por área: admin/atendimento/relatórios/`core.
  dashboard` têm `<nav>`; totem é tela cheia sem nav (logo centralizado);
  painel de TV tem `<header class="cabecalho">` só em `tv.html`, não em
  `indisponivel.html` (a outra tela da área) — melhor colocar um botão
  futuro no `_layout.html` compartilhado, não em `tv.html`.

**Implementação desta etapa (consolidação mecânica, SEM tema escuro):**

**1. Sete variáveis semânticas novas** em `gestfila.css::root` (10
existentes mantidas intocadas):

| Variável | Valor | Critério de escolha |
|---|---|---|
| `--gf-superficie` | `#FFFFFF` | Fundo de card/tabela/botão-secundário/dropdown - mesmo valor de `--gf-branco` hoje DE PROPÓSITO (só formaliza o papel semântico "superfície", que a etapa 2 vai escurecer; `--gf-branco` continua "a cor branca", constante) |
| `--gf-texto-secundario` | `#555555` | Valor mais frequente entre os 6 tons de cinza já usados (13× - convenção já era essa, só nunca nomeada) |
| `--gf-borda` | `#ECEEF2` | Valor mais frequente da família cinza-clara (10×) - NÃO inclui `#cfd6e4` (ver abaixo, papel diferente) |
| `--gf-info-fundo` | `#EAF2F8` | Único valor já usado pra esse propósito, sem mistura |
| `--gf-alerta-fundo` | `#FDF0E2` | Idem |
| `--gf-sucesso-fundo` | `#E3F6E8` | Idem |
| `--gf-nav-texto` | `#CFD6E4` | Achado durante a troca (não previsto na FASE 1) - cor de texto dos links do `<nav>` sobre fundo escuro; mantida como valor FIXO de propósito - se o `<nav>` deve seguir o tema geral ou ficar sempre escuro é decisão da ETAPA 2, não desta |

**2. ~135 substituições de hex/rgba por variável**, arquivo por arquivo,
área por área (atendimento → relatórios → core → admin → totem → base.html)
— `rgba(0,0,0,X)` de `box-shadow` deixado como estava (baixo risco,
puramente estético, per pedido). Exceções deliberadas, documentadas
inline no código pra não serem "corrigidas" por engano na etapa 2:
- `totem/senha_gerada.html::.cupom` (dentro E fora do `@media print`) -
  branco/preto fixos, o cupom deve SEMPRE parecer papel, decisão já tomada.
- `admin/_layout.html::.badge.super-admin` (`#ede4fb`/`#6b21a8`, roxo) -
  único uso de roxo no projeto inteiro, não é padrão recorrente.
- `.marcador-aberta` (`#1d7a3a`, verde "Atendendo agora",
  `_redirecionar_busca.html`/`iniciar_turno.html`) - mesmo critério, single-use.
- `.botao-totem.navegacao:hover` (`#1c2b38`, `totem/_layout.html`) - tom
  próprio de hover, único uso.
- `TipoSenha.cor` (`admin/tipos_senha/*.html`, `totem/escolher_tipo.html`)
  - é CONTEÚDO escolhido pelo admin (cor do tipo de senha), não tema -
  intocado; só o `color:#fff` fixo do texto sobre essa cor virou
  `var(--gf-branco)` (texto continua branco sempre, isso é seguro).
- `painel/_layout.html`/`painel/tv.html` (TV) - **arquivo inteiro fora do
  escopo desta etapa**, mesmo raciocínio do `--painel-*` (tema escuro fixo e
  deliberado) estendido a todo o arquivo, não só às 4 variáveis nomeadas -
  decisão de escopo registrada aqui para o cliente corrigir se a intenção
  era mais restrita.
- `#C0392B` embutido em JS (Chart.js do `core/dashboard.html`, `.style.
  color` do `painel/tv.html`) - JS não lê CSS custom properties de um
  `<style>`/`<link>` sem `getComputedStyle` explícito; fora do escopo
  MECÂNICO desta etapa (seria mudança de comportamento, não de valor) -
  documentado inline, fica pra etapa 2 se precisar reagir ao tema em runtime.

**3. `<nav>` extraído para partial único** (`app/templates/_nav_principal.
html`) — macro Jinja com `{% call %}`, não um `{% include %}` simples: cada
área continua controlando seus PRÓPRIOS itens de menu via o conteúdo do
bloco `{% call nav_principal() %}...{% endcall %}`, a macro só fornece a
casca compartilhada (logo + wrapper + link "Sair"). CSS do `<nav>`
**permanece** no `<style>` de cada layout (não movido pra `gestfila.css`
nesta etapa) — admin tem uma variante com mais links (wrap/espaçamento
próprios), unificar o CSS também exigiria escolher uma das duas variantes,
fora do escopo desta troca mecânica de valor.

**Testes:** `tests/test_regressao_consolidacao_pre_dark_mode.py` (8 casos)
- confirma as 7 variáveis novas + as 10 pré-existentes intactas no `:root`
de `gestfila.css`; confirma que as 4 áreas renderizam `<nav>` a partir do
mesmo partial (conteúdo comum presente nas 4, item exclusivo de uma área
não vaza pras outras); confirma por amostragem que a substituição chegou
ao HTML renderizado (não só ao arquivo-fonte); confirma que as exceções
deliberadas (cupom, badge roxo) continuam com hex fixo. Suíte completa
(58 arquivos pré-existentes) sem nenhuma falha — nenhum teste antigo
checava hex cru no HTML renderizado, então nenhum precisou de ajuste.

**Não incluído nesta etapa (fica para a etapa 2, dark mode em si):** botão
de alternar tema, `prefers-color-scheme`/`data-theme`, persistência em
`localStorage`, qualquer redefinição de variável por tema.

---

## 10.56. ADENDO — Dark mode em si: tema claro/escuro/automático em todo o projeto (etapa 2 de 2)

> Pedido do cliente (mesmo escopo já registrado na 10.55): dark mode em TODO
> o projeto, inclusive totem e painel de TV, com botão de alternar visível
> em TODAS as telas. **Risco do botão em telas públicas/kiosk (totem/TV) já
> aceito explicitamente pelo cliente e documentado por completo no adendo
> 10.55** — não repetido aqui, só referenciado: preferência por
> `localStorage`/navegador, sem "dono" da tela, pode ficar no tema errado
> até alguém notar.

**Mecanismo:** atributo `data-theme="light"` ou `"dark"` no `<html>`
(nunca `"auto"` — esse é só o modo GUARDADO no `localStorage`, sempre
resolvido pra um dos dois valores concretos antes de virar atributo, via
`prefers-color-scheme` quando o modo é automático). Script novo
(`app/templates/_tema_script.html`, incluído no `<head>` das 7 áreas-raiz da
FASE 1 da 10.55, **antes** do `<link>` de `gestfila.css`) aplica o tema de
forma síncrona assim que o parser chega nele — evita o flash do tema errado
no primeiro paint. Expõe `window.GestfilaTema` (não fica preso numa IIFE
fechada) porque o botão de alternar só existe mais abaixo no `<body>`,
renderizado bem depois deste script já ter rodado — sem isso o clique não
teria como chamar a mesma lógica de aplicar/persistir.

**Botão de alternar** (`app/templates/_alternador_tema.html`, macro
`alternador_tema(flutuante=False)`): um ícone só, que CICLA entre os 3
modos a cada clique (claro → escuro → automático → claro → ...), em vez de
um seletor explícito de 3 opções — cabe pequeno tanto dentro do `<nav>`
quanto flutuante em tela cheia (totem/painel de TV), e o padrão já é visto
em várias ferramentas pra essa mesma tríade (ex.: VS Code, GitHub). Estado
atual sempre visível via ícone (☀️/🌙/🖥️) + `aria-label`/`title` completos
("Tema: X. Clique para alternar."). Reaproveitado nas 7 áreas-raiz:
- Dentro do `<nav>` (admin/atendimento/relatórios/`core.dashboard`) — como
  as 4 já usam `_nav_principal.html` (10.55), um único ponto resolve as 4.
- Flutuante, canto inferior direito (`.alternador-tema--flutuante`,
  `gestfila.css`) nas 3 sem `<nav>`: `base.html` (login/erros/troca de
  senha), `totem/_layout.html`, `painel/_layout.html`.

**Paleta escura para `--gf-*`** (`gestfila.css::[data-theme="dark"]`): as
15 variáveis "temáticas" das 18 no `:root` (10 originais + 7 da 10.55)
redefinidas com valores mais claros/saturados que a versão clara — não é
auditoria formal de acessibilidade, mas contraste realmente legível, não só
"tecnicamente diferente" (pedido explícito). `--gf-branco`/`--gf-nav-texto`
continuam CONSTANTES (não redefinidas) — mesma decisão já tomada pro
nav-texto na 10.55, mantida.

> **Achado ao implementar (não previsto na 10.55):** `--gf-cinza-escuro`
> tinha DOIS papéis conflitantes — texto escuro-sobre-claro (precisa
> inverter no tema escuro) E fundo do `<nav>`/botão "navegação" do totem
> (precisa ficar FIXO, mesma barra escura em qualquer tema). Extraída uma
> variável nova, `--gf-escuro-fixo` (valor atual de `--gf-cinza-escuro`,
> NÃO redefinida no tema escuro), usada só nesses 2 lugares — libera
> `--gf-cinza-escuro` pra inverter livremente como texto em todo o resto,
> sem quebrar o `<nav>`. Achados 2 outros gaps na implementação:
> - `<input>`/`<select>`/`<textarea>` nunca tiveram `background`/`color`
>   próprios em nenhum layout (só `border`) — no tema claro isso não
>   aparecia (o padrão do NAVEGADOR já coincidia por acaso com
>   `--gf-superficie`/`--gf-cinza-escuro` claros); no escuro, ficariam
>   brancos "flutuando" numa página escura. Regra nova em `gestfila.css`
>   (`input, select, textarea { background: var(--gf-superficie); color:
>   var(--gf-cinza-escuro); }`) — sem efeito no tema claro (mesmos valores
>   que já eram aplicados por padrão), só corrige o escuro.
> - 4 usos de `background: var(--gf-branco)` como SUPERFÍCIE de card (não
>   texto) sobrevivido da 10.55 por engano — `.barra-contexto`
>   (admin/`core.dashboard`), `.cartao-modal` (totem/confirmar_geracao.html)
>   e `.senha-codigo` (totem/_layout.html). Corrigidos pra
>   `var(--gf-superficie)` (a variável certa pra essa função, que a etapa 2
>   escurece) — `--gf-branco` fica reservado só pra texto/ícone
>   literalmente branco.

**Painel de TV — paleta CLARA nova** (`painel/_layout.html`, dentro do
próprio `<style>`, não em `gestfila.css` — `--painel-*` nunca fez parte da
paleta compartilhada): a tela só tinha paleta ESCURA antes (fixa, sem
conceito de tema); ganhou `[data-theme="light"]` com 5 variáveis
`--painel-fundo`/`--painel-fundo-header`/`--painel-fundo-elemento`/
`--painel-texto-secundario`/`--painel-texto-principal` (a última é nova —
era `#fff` fixo em vários seletores, precisava de nome pra poder inverter).
A paleta escura (`:root`) continua sendo o padrão/automático — **pedido
explícito: não muda o visual atual quando o tema for escuro ou automático
com o SO em escuro.**

> **Tensão resolvida (achada ao implementar):** `painel/tv.html` usa
> `--gf-primaria`/`-escura`/`--gf-sucesso`/`--gf-alerta`/`--gf-info`
> (direto) e `--gf-cinza-medio` (via `.gf-watermark` de `gestfila.css`) na
> aparência ATUAL (valores claros/canônicos, já que nunca existiu tema
> nenhum antes). Sem tratamento especial, o bloco `[data-theme="dark"]` de
> `gestfila.css` mudaria essas cores aqui também sempre que o usuário
> escolhesse tema escuro no resto do site — violando "não muda o visual
> atual quando escuro". Resolvido PINANDO essas 6 variáveis no `:root`
> PRÓPRIO de `painel/_layout.html`, nos valores canônicos, sempre — mesma
> especificidade do `[data-theme="dark"]` de `gestfila.css` (ambos 0,1,0),
> mas como o `<style>` de `painel/_layout.html` vem DEPOIS no documento,
> vence o empate sempre, independente do tema escolhido no resto do site.
> O painel de TV fica assim com o PRÓPRIO esquema claro/escuro, totalmente
> desacoplado do tema geral admin/atendimento/etc. `.chamada-badge-
> prioritario` (fundo branco fixo, alto contraste "não importa `dados.cor`")
> continua com `background: #fff` literal, sem tocar — já é o mesmo padrão
> do cupom, e o pin de `--gf-primaria-escura` acima garante que a cor do
> texto/borda continua legível nos dois temas do painel sem precisar de
> mais nada.

**Cupom (totem/senha_gerada.html) — confirmado, não tocado:** nenhuma
variável de tema (`--gf-*` novo/velho, `--painel-*`) é referenciada em
`.cupom`, nem dentro nem fora do `@media print` — `background: #fff;
color: #000;` literal nos dois lugares, como já era desde a 10.55. Único
detalhe secundário: a borda tracejada da PRÉVIA em tela (`border: 1px
dashed var(--gf-cinza-medio)`, fora do `@media print`) usa uma variável
ORIGINAL (não uma das novas da 10.55) que passa a ter valor diferente no
tema escuro — o fundo/texto do cupom continuam 100% fixos (o que importa
pro requisito "sempre parece papel"), só essa borda fica sutilmente mais
clara no tema escuro; não alterado, registrado aqui por transparência.

**Testes:** `tests/test_regressao_dark_mode.py` (9 casos) — confirma
script+botão presentes nas 7 áreas-raiz (e antes do `<link>` de
`gestfila.css`, e a variante flutuante só nas 3 sem `<nav>`); confirma que
`[data-theme="dark"]` existe em `gestfila.css` com as 15 variáveis
temáticas DIFERENTES do `:root` claro (não é bloco vazio) e que as 3
constantes continuam iguais; confirma que `painel/_layout.html` ganha
`[data-theme="light"]` com valores diferentes da paleta escura e que os
6 `--gf-*` usados ali ficam pinados; confirma checagem estrutural do script
(lê/escreve `localStorage`, resolve automático via `prefers-color-scheme`);
confirma que o cupom nunca referencia variável de tema.

**3 testes pré-existentes corrigidos** (quebrados pelo novo `<script>` no
`<head>`, não por bug de comportamento):
`test_regressao_cupom_impressao_navegador.py`,
`test_regressao_local_rodizio_tela.py`,
`test_regressao_nome_paciente_none.py` — todos assumiam "o primeiro/único
`<script>` do documento" pra extrair só o JS de negócio da página, uma
premissa que deixou de valer com o script de tema no `<head>` (e, num caso,
também com o script de resync do botão flutuante no fim do `<body>`).
Corrigidos pra ancorar num marcador único do script que cada teste
realmente queria (`var URL_RETORNO`) ou remover TODOS os blocos
`<script>...</script>` via regex em vez de cortar a partir do primeiro -
sem mudar o que cada teste verifica, só a forma de isolar o texto certo.

**Não incluído nesta etapa:** nenhum — etapa 2 fecha o pedido de dark mode
completo (mecanismo + paleta escura `--gf-*` + paleta clara `--painel-*` +
botão nas 7 áreas). Suíte completa sem falhas.

---

## 10.57. ADENDO — Persistência de filas do turno entre logout/login (reversão PARCIAL e DELIBERADA da seção 10.23)

> **ATENÇÃO: esta seção reverte parte de um comportamento corrigido e
> documentado na seção 10.23** ("Bug corrigido: deslogar não encerrava o
> turno... login voltou a mostrar formulário zerado depois de um Sair de
> propósito"). Não é um bug novo nem uma contradição não percebida — é uma
> **mudança de comportamento CONSCIENTE, pedida e confirmada explicitamente
> pelo cliente, ciente do histórico completo da 10.23** (por que aquele
> comportamento existia, o bug real que ele corrigia). O texto da 10.23
> **não foi reescrito** - continua descrevendo, corretamente, o que foi
> decidido e por quê naquele momento. Esta seção documenta o que mudou
> depois, e por quê.

**Pedido:** só as FILAS (e o peso "chamadas seguidas" de cada uma)
configuradas na tela "Iniciar turno" devem sobreviver a um logout/login -
o operador não deveria precisar remontar a lista toda vez que loga de
novo. O **LOCAL continua sendo pedido do zero a cada login, sem nenhuma
mudança nesse ponto** - só as filas. Vale pros 3 perfis operacionais
(`atendente`/`executor`/`atendente_executor`).

**Investigação (antes de decidir a arquitetura):**
- `TurnoAtivo.filas_config` (JSON) já vive na MESMA linha que
  `TurnoAtivo.ativo` - uma linha por usuário (`unique=True` em
  `usuario_id`), NUNCA deletada; reiniciar/encerrar um turno só
  atualiza/reseta campos da mesma linha. Ou seja: **um logout nunca
  apagou `filas_config`** - só deixou de existir uma LEITURA que
  ignorasse `ativo=False` pra pré-popular o formulário.
- Não existe (nem existiu) nenhuma rota de "adicionar/remover fila em
  tempo real" - a tabela "Filas do turno" (botão "x" pra remover) é
  100% client-side (array em JS), persistida só no POST final de
  "Iniciar atendimento", que já grava em `turno.filas_config`.
- Dado isso, uma tabela nova só pra guardar "última configuração de
  filas" seria dado 100% redundante com o que já existe -
  **confirmado com o cliente antes de implementar (a suposição inicial
  de precisar de tabela nova não bateu com o que a investigação achou)
  - decisão: sem tabela nova.**

**Implementação:**
- `turno_ativo_do_usuario(usuario)` (`app/atendimento/helpers.py`) —
  **intocada**, continua filtrando `ativo=True` - usada pelo motor de
  rodízio (`_itens_rotacao`/`fila_ids_do_local`), que só deve priorizar a
  configuração do turno sobre o rodízio admin-configurado do Local quando
  há um turno DE VERDADE em andamento agora, e também usada pra
  pré-popular o LOCAL (comportamento inalterado - continua exigido do
  zero sem turno ativo).
- `ultima_configuracao_filas_do_usuario(usuario)` (nova, mesmo arquivo) —
  busca `TurnoAtivo` só por `usuario_id`, SEM filtrar `ativo`; retorna
  `filas_config` (ou `[]` se o usuário nunca configurou um turno). Usada
  SÓ pra pré-popular FILAS na tela "Iniciar turno" (GET).
- `iniciar_turno()` (`app/atendimento/turno.py`, GET) — `filas_iniciais`
  passa a vir de `ultima_configuracao_filas_do_usuario`; `local_inicial`
  continua vindo de `turno_ativo_do_usuario` (`ativo=True`), sem mudança.
- `logout()` (`app/auth/routes.py`) — **código inalterado** (continua só
  marcando `ativo=False`, nunca apagou `filas_config`) - só o comentário
  foi atualizado pra não descrever mais um efeito ("formulário
  TOTALMENTE zerado") que deixou de ser verdade pras filas.
- **Efeito colateral esperado, não escondido:** a rota de encerrar turno
  (`POST /atendimento/turno/encerrar`, `turno.py::encerrar_turno` - sem
  botão na UI desde a 10.23, mas ainda viva por URL) usa o MESMO
  mecanismo (`ativo=False`, nunca apagou `filas_config`), então também
  passa a deixar as filas pré-populadas ao voltar pra "Iniciar turno"
  (antes desta mudança isso nunca foi testado explicitamente num sentido
  ou outro) - consistente com o pedido (menos remontagem manual), não
  uma mudança pedida à parte, mas decorrência direta do mesmo fix.
- Nenhuma migration, nenhuma tabela nova, nenhuma rota nova.

**RBAC:** `admin` também está em `PERFIS_OPERACIONAIS`
(`app/atendimento/filas.py`) e usa exatamente a mesma lógica - a
persistência é por `usuario_id`, não por perfil, então não há
tratamento especial pra nenhum perfil operacional específico. Perfil
`gerencial` continua sem acesso à tela (403), sem mudança - confirmado.

**Testes:**
- `tests/test_regressao_turno_ativo.py` (seção K) — o caso "Após
  deslogar e logar de novo, lista ZERADA" **mudou de expectativa**
  (documentado inline no teste, referenciando esta seção): agora
  confirma que a fila configurada ANTES do logout continua pré-populada
  depois, e que `filas_config` no banco não muda de valor com o logout
  (só o flag `ativo`). Local continua confirmado vazio (`valorInicial:
  null`) - ponto que NÃO mudou.
- `tests/test_regressao_persistencia_filas_turno.py` (novo, 7 casos) —
  remover uma fila específica (simulando o botão "x") faz só ELA sumir,
  a outra continua com o peso certo; logout/login mantém a fila
  restante (não a removida) nos 3 perfis operacionais; Local continua
  vazio após logout, confirmado explicitamente (não só pela ausência de
  asserção contrária); RBAC de `gerencial` (403) e single-session
  (10.41, novo login derruba sessão antiga do mesmo usuário) confirmados
  sem regressão.

Suíte completa sem falhas.

---

## 10.58. ADENDO — Fila real de chamadas de voz no painel de TV (chamadas nunca se cortam)

**Pedido:** no painel de TV (`painel/tv.html`), várias chamadas chegando em
sequência rápida podiam se cortar entre si (`speechSynthesis.cancel()` era
chamado antes de cada `speak()` novo, matando a fala anterior no meio).
Decisão do cliente: chamadas nunca se cortam - tocam uma de cada vez, do
início ao fim, mesmo que isso gere um pequeno atraso em picos raros (volume
do sistema é baixo, ~50 senhas/dia no total - atraso não é problema real
aqui).

**Mecanismos relacionados (investigados antes de implementar):**
- **10.48/10.49** (corte de início de fala, CPU fraca e warm-up do motor) -
  a ORDEM falarChamada() antes de inserirChamada() dentro de cada turno da
  fila continua exatamente a mesma; a fila só decide QUANDO cada turno
  começa, não muda nada dentro dele.
- **10.50** (heartbeat do motor de speechSynthesis) - **intocado**, como
  pedido. Continua usando `speechSynthesis.speaking` (não a fila) pra
  decidir se dispara - "nada tocando" agora inclui "fila vazia"
  naturalmente, porque a fila serializa tudo através do mesmo
  `speechSynthesis.speak()`.
- **10.51** (despertar hardware de áudio via Web Audio API,
  `despertarSaidaAudio`) - continua existindo exatamente igual, só passou a
  ser chamado condicionalmente (ver decisão 1 abaixo), não mais sempre.

**Duas decisões de produto levantadas e confirmadas explicitamente com o
cliente antes de escrever qualquer código** (as duas opções e o porquê da
escolha):

1. **Tom de despertar (despertarSaidaAudio) em cada chamada da fila, ou só
   na 1ª de uma rajada?**
   - Opção A (escolhida): só na 1ª chamada de uma rajada (pipeline
     realmente ocioso antes dela - fila vazia E nada em andamento no
     momento em que ela chegou). Chamadas seguintes da mesma rajada pulam
     o tom - o hardware acabou de tocar algo segundos atrás, está
     garantidamente "quente". Menos ~130ms de atraso por chamada extra na
     fila.
   - Opção B (rejeitada): tocar sempre, mesmo em chamadas já enfileiradas
     atrás de outra. Mais uniforme, mas empilha atraso comprovadamente
     desnecessário numa rajada.
2. **Atualização visual (inserirChamada) de uma chamada na fila: imediata
   ao chegar, ou só quando o ÁUDIO dela começa?**
   - Opção A (escolhida): só quando o áudio daquela chamada específica
     começa (turno processado). Tela e voz sempre sincronizadas - nunca
     mostra uma senha que a voz ainda não anunciou. Numa rajada rara, a
     tela "atrasa" junto com a fila de áudio (mesmo atraso já aceito pra
     voz).
   - Opção B (rejeitada): atualizar a tela assim que o evento chega do
     servidor, independente da fila de áudio. Podia gerar descompasso
     temporário (tela mostrando a chamada 3 enquanto a voz ainda termina a
     1).

**Implementação** (`app/templates/painel/tv.html`):
- `filaChamadas` (array) + `processandoFila` (bool) - novo estado no topo
  do script. `processandoFila` é true do início ao fim do "turno" de UMA
  chamada (tom opcional + `falarChamada()` + `inserirChamada()` + esperar o
  áudio terminar de verdade) - nunca dois turnos ao mesmo tempo.
- `enfileirarChamada(dados)` - chamada por `socket.on('chamada')` no lugar
  do disparo direto de áudio/visual. Decide `tocarTom` NO MOMENTO EM QUE A
  CHAMADA CHEGA (`!processandoFila && filaChamadas.length === 0` - decisão
  1 acima), empurra `{dados, tocarTom}` pra fila, chama
  `processarProximaDaFila()`.
- `processarProximaDaFila()` - guard `if (processandoFila) return;` evita
  dois turnos simultâneos; tira o próximo item da fila; se `tocarTom`,
  envolve o turno em `despertarSaidaAudio()` (10.51), senão processa
  direto; dentro do turno, `falarChamada(item.dados, finalizarTurno)`
  roda antes de `inserirChamada(item.dados)` (ordem da 10.48, decisão 2
  acima); `finalizarTurno()` (chamado quando a fala termina de verdade)
  libera `processandoFila` e agenda o próximo turno via
  `setTimeout(processarProximaDaFila, 0)` (evita empilhar a pilha de
  chamadas síncronas em rajadas maiores, sem efeito no timing real - a
  espera é imposta pelo áudio, não por este `setTimeout`).
- `falarChamada(dados, aoTerminar)` e `falarChamadaPregravada(dados,
  aoTerminar)` passam a aceitar um callback de conclusão, chamado
  exatamente uma vez quando a fala termina de tocar - **inclusive nos
  early-returns sem áudio real** (som desativado, sem suporte a
  `speechSynthesis`, sem voz pt-* instalada, TV pré-gravada): sem isso a
  fila travaria pra sempre esperando uma chamada que nunca ia "terminar".
  Pro caminho `speechSynthesis`, a conclusão real é `fala.onend`/
  `fala.onerror` (evento do motor, não `speechSynthesis.speaking`
  isolado nem um tempo estimado - pedido explícito, por causa de
  comportamento inconsistente de `speaking` entre navegadores em alguns
  casos relatados).
- `speechSynthesis.cancel()` **deixa de rodar incondicionalmente antes de
  todo `speak()`** ("evitar empilhar falas" agora é responsabilidade da
  fila, que só inicia uma chamada nova depois que a anterior terminou de
  verdade - cancelar aqui cortaria exatamente a fala que a fila está
  deixando terminar de propósito). Continua existindo só como **proteção
  de borda**: um watchdog (`WATCHDOG_FALA_TIMEOUT_MS = 15000`) chama
  `cancel()` e força a conclusão do turno SE `onend`/`onerror` nunca
  disparar dentro de 15s (motor preso num estado inconsistente) - nunca
  como mecanismo normal de transição entre chamadas.
- Comentário da 10.50 (heartbeat) corrigido: descrevia "o `cancel()` que
  `falarChamada()` já faz" cancelando um heartbeat concorrente - essa
  frase descrevia um mecanismo que deixou de existir com este adendo;
  corrigida pra refletir o comportamento atual (a chamada real entra na
  fila normalmente, o heartbeat curtíssimo termina antes dela).

**Testes:**
- `tests/test_regressao_fila_voz_tv.py` (novo, estrutural - mesma
  limitação de infraestrutura dos outros testes de JS do projeto, sem
  runtime de navegador): confirma o estado da fila, que `socket.on
  ('chamada')` só enfileira, a decisão de `tocarTom` na chegada (decisão
  1), a ordem falarChamada()->inserirChamada() dentro do turno (decisão
  2), que os dois callbacks de conclusão cobrem todos os early-returns, e
  que `cancel()` não roda mais incondicionalmente. Inclui uma simulação
  em Python da regra de `tocarTom` com uma rajada de 3 chamadas
  (confirma só a 1ª toca o tom) e uma chamada isolada (confirma que
  continua com a mesma latência de hoje, tom sempre toca sem
  concorrência).
- `tests/test_regressao_despertar_audio.py` e
  `tests/test_regressao_audio_pregravado.py` - trechos que checavam
  `despertarSaidaAudio()`/`falarChamada()` sendo chamados direto de
  dentro de `socket.on('chamada')` foram atualizados pra refletir que
  agora só `enfileirarChamada()` roda ali; a chamada real acontece em
  `processarProximaDaFila()` - não-regressão confirmada nos dois
  arquivos.

Suíte completa (62 arquivos) sem falhas.

---

## 10.59. ADENDO — 3 achados de uma instalação real em produção (Ubuntu 24 LTS, seguindo o README do zero)

> Diferente dos adendos anteriores, os 3 itens abaixo **não vieram de revisão
> de código nem de teste automatizado** - vieram de seguir o guia "Instalação
> em produção" do README passo a passo, num servidor Ubuntu 24 LTS limpo,
> pela primeira vez. Nenhum é um bug de comportamento do GESTFILA em si -
> são gaps do processo de deploy/dependências que só aparecem no caminho
> real de uma instalação nova, não em `python -m tests.*` nem em uso normal
> do sistema já rodando.

**1) `requirements.txt` não listava o `gunicorn`.** Fazia sentido pra
desenvolvimento/teste local (a suíte usa `python -m tests.*`, nunca sobe um
servidor WSGI de produção) - mas quebra silenciosamente uma instalação nova:
o erro só aparece no `systemctl start`, como `203/EXEC` genérico, sem
apontar pra causa raiz (o `venv` simplesmente não tem o executável
`gunicorn`). `eventlet` já estava listado (confirmado com `pip install
eventlet` respondendo "already satisfied" logo após `pip install -r
requirements.txt`) - só o `gunicorn` em si faltava.

**2) `gunicorn==26.0.0` removeu o worker `eventlet`.** Achado e reproduzido
no servidor real: a versão mais nova do PyPI na hora da instalação (26.0.0)
não tem mais `gunicorn.workers.geventlet` (confirmado via `ls
venv/.../gunicorn/workers/` - sem `geventlet.py` na lista) e recusa até o
extra `pip install "gunicorn[eventlet]"` (warning explícito do pip: "gunicorn
26.0.0 does not provide the extra 'eventlet'"). `gunicorn==22.0.0` testado
no MESMO servidor, MESMO `venv`, MESMO comando (`--worker-class eventlet`) -
subiu limpo ("Using worker: eventlet", sem erro).

**Decisão do pin:** investigado antes de decidir - nenhum outro lugar do
projeto (`deploy/gunicorn.conf.py`, CI, `run.py`) já fixava uma versão de
Gunicorn; não havia uma referência pra reaproveitar. Optado por
**pin exato** (`gunicorn==22.0.0`), não uma faixa (`gunicorn<23`) - sem
acesso a uma pesquisa que confirmasse que TODA a série 22.x/pré-23 mantém o
worker eventlet, o único valor com confirmação real de funcionar hoje é
exatamente esse. Comentário no próprio `requirements.txt` documenta o
porquê e avisa para não presumir que a faixa inteira funciona sem testar de
novo antes de mudar o pin.

**3) Guia do README - `SEU_IP_INTERNO` aparece 3 vezes no bloco do Apache**
(as duas linhas `ServerName` + a linha `Redirect permanent`, passo 11) e,
na instalação real, as duas linhas `ServerName` foram trocadas mas a linha
`Redirect` foi esquecida - o `apache2ctl configtest` não acusa nada (a
sintaxe continua válida com o placeholder literal ali), o Apache sobe
normal, e o único sintoma é o redirecionamento HTTP→HTTPS simplesmente não
acontecer - silencioso o bastante pra passar despercebido na primeira
instalação. Adicionada uma nota explícita no passo 11 do README avisando
das 3 ocorrências antes do bloco de configuração.

**Implementação:**
- `requirements.txt` - adicionado `gunicorn==22.0.0` (com o comentário do
  porquê do pin exato), logo após `eventlet` (os dois trabalham juntos em
  produção - `gunicorn --worker-class eventlet`).
- `README.md` (seção "Instalação em produção", passo 11) - nota sobre as 3
  ocorrências de `SEU_IP_INTERNO` adicionada antes do bloco `apache`.

**Testes:** suíte completa reexecutada após reinstalar as dependências
localmente (com `gunicorn` agora presente no `venv`) - nenhum teste depende
da ausência do Gunicorn no ambiente, nenhuma regressão.

Suíte completa sem falhas.

---

## 10.60. ADENDO — Criação de super admin global via UI segue o mesmo padrão de senha do seed (temporária + troca obrigatória)

> Pedido: a criação de um segundo super admin pelo painel (adendo 10.9) já
> existia, mas divergia do único outro lugar que cria um super admin
> (`seeds/seed_inicial.py`, adendo 10.35) - lá a senha inicial é sempre
> temporária/aleatória, nunca escolhida por quem executa o seed. Na rota da
> UI, quem criava digitava a senha final na hora, sem forçar troca -
> inconsistente com o próprio padrão de segurança já adotado pro superadmin
> original. Investigado antes de implementar (RBAC, origem do `empresa_id`,
> unicidade de login) - achados abaixo.

**RBAC unificado:** `novo_usuario_global` (`app/admin/usuarios.py`) trocou a
checagem manual `if not g.is_super_admin: abort(403)` pelo decorator
`@super_admin_required` (`app/utils/decorators.py`), o mesmo já usado em
`admin/empresas.py` e `admin/perfis.py` - mesmo efeito, sem checagem nova.

**Senha temporária + troca obrigatória:** a rota gera a senha com
`secrets.token_urlsafe(12)` (mesma chamada do seed) e marca
`usuario.deve_trocar_senha = True` - o middleware genérico
`app/middleware/forcar_troca_senha.py` (adendo 10.35) já bloqueia qualquer
tela até a troca, sem precisar de nada novo além de acionar a flag. Qualquer
valor de `senha` que o cliente envie no POST é ignorado (o formulário
(`admin/usuarios/form_global.html`) nem mostra mais o campo, ao criar - só ao
editar um super admin já existente, onde a troca de senha continua opcional
e explícita).

**Exibição única da senha:** a senha temporária só existe na própria resposta
HTML do POST de criação (`admin/usuarios/global_criado.html`) - nunca é
salva em sessão/flash/log, só no hash (`Usuario.senha_hash`). Como um F5 do
navegador reenviaria o mesmo POST (criando outro usuário), a tela de
confirmação não tem link de "recarregar", só um link de volta para
`/admin/usuarios`.

**`empresa_id` e unicidade de login (confirmado, sem mudança):**
`novo_usuario_global` sempre grava `Usuario(empresa_id=None)` direto, nunca
depende de `resolver_empresa_contexto()` (usada pela criação de usuário
normal, `novo_usuario`) - não existe caminho pra "esquecer" a empresa por
acidente. `Usuario.login` já é `unique=True` **sem** escopo de empresa
(`app/models/usuario.py`), então a validação de duplicidade existente
(`Usuario.query.filter_by(login=login).first()`) já cobre corretamente um
super admin colidindo com o login de qualquer usuário do sistema, de
qualquer empresa.

**Arquivos:** `app/admin/usuarios.py` (`novo_usuario_global`),
`app/templates/admin/usuarios/form_global.html` (campo de senha só ao
editar), `app/templates/admin/usuarios/global_criado.html` (novo - tela de
confirmação). **Testes:** `tests/test_regressao_super_admin_global.py`
estendido - formulário de criação sem campo de senha; senha enviada pelo
cliente é ignorada; usuário nasce com `deve_trocar_senha=True`; senha
temporária exibida na resposta do POST bate com o hash gravado; login com
essa senha cai no fluxo de troca obrigatória (mesmo comportamento do
seed, adendo 10.35); RBAC (403 pra admin de empresa, mesmo por URL direta)
inalterado. Suíte completa (62 módulos) reexecutada sem falhas.

---

## 10.61. ADENDO — Painel de TV preso em "Reconectando..." em produção: faltava `X-Forwarded-Proto` no vhost do README (mesmo padrão da 10.59/10.60)

> Achado numa instalação real, não em teste automatizado (mesma origem dos
> achados da 10.59): Apache, SSL e Gunicorn todos funcionando
> individualmente (`systemctl status gestfila` ok, `apache2ctl configtest`
> ok, HTTPS abrindo normalmente), mas o painel de TV nunca saía de
> "Reconectando...". Causa raiz confirmada via log real do servidor
> (`journalctl -u gestfila`), não suposição:
>
> ```
> "https://SEU_IP_INTERNO is not an accepted origin."
> ```

**Causa raiz:** o vhost de exemplo do `README.md` (seção "Instalação em
produção", passo 11) nunca setava `X-Forwarded-Proto`. Sem esse header, o
Gunicorn (atrás do Apache) só enxerga a conexão como HTTP simples - mesmo o
navegador tendo chegado em HTTPS -, e o Flask-SocketIO rejeita o handshake
do WebSocket por origem inconsistente (`https://...` no browser vs. `http://...`
no que o backend acha que recebeu). **Não é** o problema já corrigido de
`ProxyPass ws://` vs `http://` (esse já está certo no vhost do README, linhas
`ProxyPass /socket.io/ ws://...` + `ProxyPass / http://...`, mesmo padrão do
`deploy/apache-painel-senhas.conf` de referência) - é uma causa diferente,
mais adiante no mesmo fluxo: o handshake chega no Gunicorn com o proxy
certo, mas com o esquema errado.

**Confirmado, não é falha do código Python (sem mudança em `app/`):** o
projeto já tem a peça que deveria consumir esse header -
`ProxyFix(x_for=1, x_proto=1)` + `TRUST_PROXY_HEADERS=True` em
`ProductionConfig` (`app/__init__.py`/`app/config.py`, adendo 10.25) - e ela
já está configurada corretamente (exatamente 1 hop, `x_proto=1`). O código
nunca teve a chance de confiar em nada porque o Apache simplesmente não
mandava o header nenhuma vez; não há causa mais funda mascarada por essa
correção - uma vez que o Apache manda `X-Forwarded-Proto: https`, o
`ProxyFix` já existente consome corretamente, sem qualquer ajuste adicional.
O `deploy/apache-painel-senhas.conf` (deploy de referência anterior, mais
antigo que o guia do README) **já tinha essa linha** desde sempre - o gap
era só no vhost simplificado do README, que o próprio README já avisava
"confira `deploy/` antes de divergir" (linha 78) sem essa linha
especificamente ter sido carregada para o guia novo.

**Implementação (só configuração de servidor, nenhuma mudança em código):**
- `README.md` (seção "Instalação em produção", passo 11) - adicionada
  `RequestHeader set X-Forwarded-Proto "https"` dentro do
  `<VirtualHost *:443>`, logo após `ProxyPreserveHost On`, com comentário
  citando o sintoma exato ("Reconectando..." pra sempre) para reconhecimento
  rápido numa próxima instalação.
- `mod_headers` (pré-requisito do `RequestHeader`) **já estava** no comando
  `a2enmod` do passo 2 (`sudo a2enmod proxy proxy_http proxy_wstunnel ssl
  headers rewrite`) - confirmado, nenhuma mudança necessária ali.

**Sem mudança de código** - só documentação/config de exemplo; suíte de
testes não roda contra um vhost Apache real, não há teste automatizado
possível para esse achado (mesmo racional da 10.59, itens 1-3).

---

## 10.62. ADENDO — Tempo de atendimento mede TODOS os segmentos de uma senha realocada (não só o 1º e o último)

> Pedido do cliente: uma senha realocada mais de uma vez (ex.: Recepção →
> Fila A → Fila B → Finalizado) deve ter os 3 segmentos medidos no relatório
> de tempo de atendimento, cada um com fila/local corretos - não só o
> primeiro (chamada → 1ª realocação) e o último (última chamada →
> `fim_atendimento`), como documentado antes na seção 7 do BLUEPRINT
> (limitação assumida ali, agora superada para dado novo).

**Decisões de produto confirmadas antes de implementar:**
- Segmentos de qualquer duração contam no tempo médio, mesmo poucos segundos
  - sem filtro de "provável erro de clique".
- **Dados históricos (atendimentos já registrados antes deste deploy) NUNCA
  ganham a granularidade nova** - continuam exibidos exatamente como antes
  (1º/último segmento, "—" quando aplicável). Só atendimentos cujos eventos
  de realocação forem gravados a partir deste deploy têm os N segmentos
  corretos, com fila/local reais. Decisão consciente, documentada aqui - não
  é uma lacuna a "corrigir" depois com uma migração de dados retroativa.

**Parte 1 - Snapshot de fila/local por evento** (pré-requisito, sem o qual
não dá pra saber ONDE um segmento intermediário aconteceu):
- Migration `9c1f6a3e5d7b` adiciona `logs_atendimento.fila_id`/`local_id`
  (FK `filas.id`/`locais.id`, **nullable, sem migração de dados** - linhas
  antigas ficam `NULL` pra sempre, de propósito).
- Levantados os **7 pontos** do código que gravam em `logs_atendimento`
  (confirmado lendo o código, não assumido): `gerada`
  (`app/totem/routes.py`), `chamada_nome`/`chamada_senha`/`realocado`/
  `voltou_aguardar`/`fim_atendimento` (`app/atendimento/helpers.py`) e
  `encerramento_automatico_sistema` (`app/middleware/encerramento.py`).
  `inicio_atendimento` está no enum mas nunca é gravado - sem mudança, já
  documentado no topo de `app/relatorios/queries.py`. Todos os 7 passaram a
  gravar `fila_id`/`local_id` da `Senha` no momento exato daquele evento.
- **Dois pontos exigiram capturar o valor ANTES da mutação da própria ação**
  (os outros 5 já liam o estado correto no momento de criar o log, sem
  mudança de ordem): `registrar_realocacao` grava a fila/local de **ORIGEM**
  (`fila_origem_id`/`local_origem_id`, capturados antes de mover a senha
  pra `fila_destino`/limpar `local_id`) - é esse par que fecha o segmento
  que terminou agora, não o destino pra onde a senha está indo.
  `registrar_volta_espera` captura `local_id` antes de zerá-lo, pela mesma
  razão.

**Parte 2 - Generalização para N segmentos**
(`app/relatorios/queries.py::_segmentos_intermediarios_realocacao`,
substitui `_pares_inicio_realocado`): em vez de pegar só o primeiro
`realocado` de cada senha, percorre TODOS os eventos de fronteira
(`chamada_senha`/`chamada_nome`/`realocado`) em ordem cronológica com um
único ponteiro `inicio_pendente` - toda realocação exige a senha estar em
status chamada/em_atendimento primeiro (ou seja, sempre existe uma chamada
nova na fila de destino antes da PRÓXIMA realocação), então o pareamento
sequencial nunca pula um segmento. Uma senha realocada N vezes gera
exatamente N segmentos intermediários aqui, mais 1 final (via
`_pares_inicio_fim`, sem mudança) = N+1 no total.

**Abordagem escolhida: iteração em Python, não self-join/`LAG`/`LEAD` em
SQL.** Mesma filosofia já declarada no topo do arquivo (`app/relatorios/
queries.py`) - agregação em Python mantém o código idêntico em SQLite
(testes) e MySQL (produção), simples de testar, e o volume do projeto não
justifica SQL específico de banco. A bifurcação "dado novo vs. histórico"
(abaixo) também seria bem mais incômoda de expressar em SQL portável do que
como um `if`/`else` em Python.

**Fallback pra dado histórico, decidido POR SENHA (não por evento
isolado):** se QUALQUER `realocado` de uma senha não tiver o snapshot
(`fila_id IS NULL` - log de antes deste deploy), a senha inteira usa a regra
ANTIGA - só o primeiro segmento, sem fila/local (só `unidade_id`), igual
`_pares_inicio_realocado` fazia. Cobre o caso normal (senha 100% antes ou
100% depois do deploy) com uma regra simples; o caso raro de uma senha em
atendimento no momento exato do deploy (parte dos logs de cada lado) cai no
mesmo fallback antigo - mais seguro que misturar segmentos parciais, e
documentado aqui como a ressalva pedida (incerteza reconhecida, não
escondida).

**Operador de cada segmento:** confirmado sem necessidade de mudança - é o
`usuario_id` do evento que ABRE aquele segmento (`chamada_senha`/
`chamada_nome`), que já é sempre o operador que de fato realizou aquela
chamada específica, inclusive para chamadas em filas de destino após uma
realocação. Nenhum campo novo foi necessário para isso.

**`_pares_inicio_fim` (segmento final) não mudou** - já estava correta para
dado novo e antigo (nada muta `fila_id`/`local_id` entre a última chamada e
o Finalizar, então a aproximação pelo estado atual da `Senha` já era exata).
`ranking_operadores()` (relatório 4) também não mudou - continua usando só
`_pares_inicio_fim` (tempo do último segmento), fora do escopo deste pedido.

**Arquivos:** `migrations/versions/9c1f6a3e5d7b_*.py` (novo),
`app/models/log_atendimento.py` (colunas `fila_id`/`local_id` +
relationships), `app/totem/routes.py`, `app/atendimento/helpers.py`,
`app/middleware/encerramento.py` (os 7 pontos de gravação),
`app/relatorios/queries.py` (`_segmentos_intermediarios_realocacao`
substitui `_pares_inicio_realocado`; `tempo_atendimento` atualizada pra
consumir fila/local reais quando disponíveis).

---

## 10.63. ADENDO — "Conclusão" vs. "Repasse": tempo_atendimento() misturava dois tipos de segmento na mesma linha (achado em revisão do 10.62)

> **Não foi uma decisão original - foi um efeito colateral encontrado em
> revisão do adendo 10.62, apontado antes da aprovação final.** Antes da
> 10.62, um segmento intermediário (que termina em `realocado`) sempre tinha
> `fila_id=None` na chave de agrupamento de `tempo_atendimento()` - o que, por
> acidente, também impedia essa linha de colidir com a de um segmento final
> (que termina em `fim_atendimento`, sempre com `fila_id` real). A 10.62 deu
> fila/local REAIS aos segmentos intermediários (o ponto central daquele
> adendo) - e, sem perceber, apagou essa separação incidental: um operador
> que às vezes finaliza e às vezes repassa na MESMA fila/local passou a ter
> os dois tempos médios misturados numa única linha, sem forma de diferenciar
> "tempo até concluir de verdade" de "tempo até só repassar adiante".

**Correção:** `tipo_fechamento` entra na chave de agrupamento de
`tempo_atendimento()` (`app/relatorios/queries.py`), sempre - mesmo quando
operador/setor/fila/local coincidem entre uma linha de `_pares_inicio_fim`
(fecha em `fim_atendimento`) e uma de `_segmentos_intermediarios_realocacao`
(fecha em `realocado`). Dois valores possíveis, centralizados como
constantes (não strings soltas espalhadas por `queries.py`/`routes.py`/
templates, mesmo padrão de `EVENTO_LOG`/`SETOR_LOG` em
`app/models/log_atendimento.py`): `TIPO_FECHAMENTO_CONCLUSAO = "conclusao"` e
`TIPO_FECHAMENTO_REPASSE = "repasse"`, mais `TIPO_FECHAMENTO_ROTULOS` (dict
de rótulo pro humano: "Conclusão"/"Repasse") - as três em
`app/relatorios/queries.py`, importadas por `app/relatorios/routes.py` (não
redefinidas lá).

**Exposição na tela/export (não só campo interno do dict):** nova coluna
"Tipo" em `app/templates/relatorios/tempo_atendimento.html`, entre "Local" e
"Quantidade" - badge reaproveitando o MESMO padrão visual já usado em
`volume.html` (`.badge.prioridade`/`.badge.normal`, definidos em
`relatorios/_layout.html`): `.badge.conclusao` usa a paleta "positiva" já
existente (verde-primária), `.badge.repasse` usa o neutro já existente
(cinza) - nenhuma cor nova introduzida. CSV/Excel (`tempo_atendimento_view`,
`app/relatorios/routes.py`) ganham uma coluna `tipo` com o mesmo rótulo
("Conclusão"/"Repasse"), ao lado das colunas já existentes.

**Testes:** `tests/test_regressao_segmentos_realocacao.py` estendido -
cenário novo com o MESMO operador/fila/local, uma senha concluída
(`fim_atendimento`, 200s) e outra repassada (`realocado`, 10s): confirma
exatamente 2 linhas (`tipo_fechamento` "conclusao" e "repasse"), cada uma com
sua própria média (200s e 10s), não uma única linha misturada (ex.: uma
média incorreta de 105s). Os 4 segmentos do teste de 3 realocações também
ganharam asserção de `tipo_fechamento` (3 "repasse" + 1 "conclusao" final).
`ranking_operadores()` (relatório 4) não usa `tipo_fechamento` - continua
agrupando só por operador (sem `fila_id` na chave), fora do escopo deste
achado.

---

## 10.64. ADENDO — "Botão pai" e "Fila" na tela de Botões do totem não filtravam por Unidade (mesma classe de bug do 10.53)

> Bug real relatado pelo cliente, com prints: nos campos "Botão pai" e "Fila"
> de `admin/totem_botoes/form.html`, era possível selecionar um botão-pai ou
> uma fila de OUTRA unidade por engano. Mesma classe de bug já corrigida no
> checklist "Filas de destino permitidas" de `admin/locais/form.html`
> (adendo 10.53) - investigado antes de assumir que a correção seria
> idêntica, porque não era: aqui são dois `<select>` (seleção única), não um
> checklist de checkboxes, e havia uma segunda regra de negócio faltando
> (item 3 abaixo) que o 10.53 não tinha.

**1. Campo "Unidade" é editável de verdade nas DUAS telas:**
`editar_totem_botao()` (`app/admin/totem_botoes.py`) grava
`botao.unidade_id = dados["unidade_id"]` sem restringir à unidade original
do botão - só valida que pertence à mesma empresa
(`unidades_da_empresa(empresa)`). Ou seja, o filtro precisa ser em tempo
real (JS no `change` do select "Unidade"), não um filtro estático feito uma
vez no carregamento - igual à tela "Novo botão".

**2. Nenhum `data-unidade-id` existia nos selects "Botão pai"/"Fila"** -
adicionado do zero em cada `<option>` (e em cada `<optgroup>` do select de
Fila, que já agrupava por unidade via `filas|groupby('unidade.nome')`).

**3. Regra de negócio faltando, achada nesta investigação (não só o filtro
de unidade): "Botão pai" precisa ser um botão-MENU (`fila_id` nulo), nunca
um botão-folha.** `_candidatos_pai()` só excluía o próprio botão e seus
descendentes (evita ciclo na hierarquia) - não impedia escolher um
botão-folha como pai. Um botão-folha vira uma ação de gerar senha no totem
(`_decorar_botoes`/`app/totem/routes.py`: `gera_senha = botao.fila_id is
not None`), nunca uma entrada de submenu - um botão colocado como filho
dele ficaria inalcançável na navegação real do totem, mesmo que a
hierarquia fosse tecnicamente válida no banco. Corrigido filtrando
`fila_id=None` na própria query de `_candidatos_pai` - isso também fecha o
lado servidor de graça: `_validar_totem_botao` já rejeita qualquer
`parent_id` que não esteja em `candidatos_pai`, então um botão-folha
enviado via POST direto (bypassando o form) também passa a ser rejeitado,
não só escondido da tela. Auto-referência (o próprio botão como pai dele
mesmo) e ciclos via descendentes **já eram tratados corretamente antes**
desta correção - confirmado, não precisou de mudança.

**4. Arquitetura da sigla (10.47) fora do escopo, confirmado:** `Fila`
pertence a uma unidade e o filtro por unidade cobre isso normalmente, mas
`TotemBotao.sigla` (não `Fila.sigla`, deprecada) não tem nenhuma relação
com este bug - nada nessa parte foi tocado.

**Correção:**
- `app/admin/totem_botoes.py::_candidatos_pai` - acrescenta `fila_id=None`
  ao filtro da query (item 3).
- `app/templates/admin/totem_botoes/form.html` - `data-unidade-id` em cada
  `<option>` de "Botão pai" e "Fila" (e em cada `<optgroup>` de "Fila");
  script novo que filtra (`hidden`, nunca remove do DOM) as opções que não
  batem com a unidade escolhida no `<select id="unidade_id">`, tanto no
  load quanto a cada `change` - mesmo padrão do 10.53. Uma seleção que sai
  de vista é resetada (`select.value = ""`), não fica "escondida mas
  marcada" até o submit. Como `<option>`/`<optgroup>` não têm nenhum
  `display:` de autor em `gestfila.css` (confirmado antes de aplicar -
  ver a recorrência do bug `[hidden]` vs. `display` de autor documentada
  no 10.53), o `hidden` nativo funciona aqui sem precisar de uma regra CSS
  própria, diferente dos três casos anteriores daquele padrão.

**Testes:** novo `tests/test_regressao_totem_botoes_filtro_unidade.py` -
`data-unidade-id` presente nas duas telas (novo/editar) para as opções das
duas unidades; troca de Unidade no formulário (confirmado via HTML
renderizado, mesmo estilo dos demais testes deste projeto) restringe as
opções disponíveis; um botão-folha nunca aparece na lista de candidatos a
pai (nem no HTML, nem aceito no POST); o próprio botão não aparece como
opção de pai dele mesmo na edição; uma fila/pai de outra unidade
selecionada continua rejeitada no servidor (`_validar_totem_botao`, já
existia, teste novo só confirma que não regrediu). Suíte completa sem
falhas.

**Arquivos alterados:** `app/admin/totem_botoes.py` (`_candidatos_pai`),
`app/templates/admin/totem_botoes/form.html` (atributos + script), novo
`tests/test_regressao_totem_botoes_filtro_unidade.py`.

---

## 10.65. ADENDO — Totem esconde botões de fila fora do horário de funcionamento, atualizando sozinho sem F5

> Nova feature pedida pelo cliente: em vez de deixar um botão de fila
> clicável fora do horário configurado (`Fila.horario_funcionamento`, seção
> 10.1) - o toque leva à tela "fora do horário de funcionamento" só depois de
> navegar, `confirmar_geracao()`/`app/totem/routes.py` -, o botão deve
> simplesmente **sumir** da tela enquanto a fila estiver fechada, e reaparecer
> sozinho quando ela abrir, sem precisar de F5 (a tela do totem fica aberta o
> dia inteiro sem interação nenhuma). Decisões já confirmadas com o cliente:
> um botão-MENU também some se TODOS os seus descendentes-folha estiverem
> fora do horário no momento.

**Investigação (antes de implementar - 2 premissas do pedido original não se
confirmaram):**

1. **Formato de `horario_funcionamento` confirmado:** dict por dia da semana
   (`app/models/fila.py::DIAS_SEMANA`, 7 dias), cada um com só DOIS períodos
   booleanos (`manha` = 00:00–11:59, `tarde` = 12:00–23:59) - sem horário
   exato, sem múltiplos intervalos por dia.

2. **Não existe (nem existiu) uma reimplementação de `Fila.aberta_em()` em
   JavaScript neste projeto** - premissa do pedido original. Os 3 lugares que
   mostram "● Atendendo agora" (adendo 10.10: `iniciar_turno.html`,
   `_redirecionar_busca.html`, `dashboard_queries.py`) recebem um booleano JÁ
   calculado pelo SERVIDOR no render (`"aberta": {{ fila.aberta_em() |
   tojson }}`) - um retrato estático da hora do request, nunca recalculado no
   cliente. Não havia uma "2ª versão JS" pra reaproveitar.

3. **Consequência de (2): não havia uma decisão já tomada sobre fuso no
   cliente pra replicar.** Dado o histórico deste projeto (10.37/10.39/10.54 -
   sempre bugs por resolver hora/dia FORA do código central de fuso,
   `app/utils/tempo.py`), fazer o NAVEGADOR do totem recalcular dia/período
   reabriria o mesmo risco: um totem físico com o relógio/fuso do SO mal
   configurado erraria silenciosamente - exatamente a categoria de bug que a
   10.37 eliminou ao mover a decisão de fuso pra dentro do código
   (`FUSO_HORARIO`), longe de qualquer configuração ambiente.

4. **Achado que mudou a arquitetura da correção:** o totem é multi-página -
   cada nível de submenu é uma navegação de SERVIDOR separada
   (`totem/routes.py::submenu`). Um botão-menu na tela raiz pode ter
   descendentes-folha vários níveis abaixo que NUNCA chegam ao HTML da tela
   atual (só ao navegar pra dentro). Calcular a cascata "todos os
   descendentes-folha fora do horário" no cliente é estruturalmente
   impossível sem os dados de páginas ainda não carregadas.

Diante de (2)-(4), a abordagem foi confirmada com o cliente antes de
implementar: cálculo 100% no SERVIDOR (reaproveitando a mesma
`Fila.aberta_em()` de sempre - zero lógica de dia/período nova, zero
reimplementação divergente), exposto de duas formas (mesma função,
`_status_visibilidade_botoes`, chamada duas vezes):
- **Embutido inicial:** `tela_raiz()`/`submenu()` passam
  `status_visibilidade={{botao_id: bool}}` pro template - primeira pintura já
  correta, sem esperar uma chamada de rede.
- **Endpoint de poll:** `GET /totem/<slug>/status-visibilidade-filas` (JSON
  leve, `{botao_id: bool}` de TODOS os botões ativos da unidade) - o JS de
  `menu.html` consulta a cada 45s (`setInterval`) e reaplica `hidden`, sem
  reload. O relógio do navegador do totem nunca entra na conta - só decide
  QUANDO perguntar de novo, nunca O QUE responder.

**Regra de cálculo (`_status_visibilidade_botoes`, `app/totem/routes.py`):**
- Botão-FOLHA (`fila_id` preenchido): visível se `fila.aberta_em()`. **Fila
  SEM `horario_funcionamento` configurado (vazio/`None`) é sempre considerada
  ABERTA** - ausência de configuração nunca esconde um botão (fallback pedido
  explicitamente, mesmo a coluna tendo um default que normalmente evita esse
  caso).
- Botão-MENU (`fila_id` nulo): visível se não tiver NENHUM descendente-folha
  (puramente navegacional, nada aqui pra avaliar - nunca escondido por esta
  lógica) OU se PELO MENOS UM descendente-folha, em QUALQUER profundidade
  (não só filhos diretos), estiver visível.

> **Achado de CSS (4ª ocorrência do mesmo padrão já documentado 3x nesta
> seção 10.53):** `.botao-totem { display: flex; ... }`
> (`app/templates/totem/_layout.html`) é uma regra de origem AUTOR - um
> `hidden` nativo aplicado direto num `<a class="botao-totem">` seria
> silenciosamente ignorado (`[hidden] { display: none }` é regra de origem
> UA, sempre perde de qualquer `display:` de autor no mesmo elemento,
> independente de especificidade). Corrigido com `.botao-totem[hidden] {
> display: none; }`, mesma técnica das 3 ocorrências anteriores. O `<form>`
> que envolve um botão-folha de submit direto não precisou da mesma correção -
> não tem nenhum `display:` de autor no seletor `form` deste template.

**UX adicional (não pedida explicitamente, mas mesma lógica da mensagem vazia
já existente):** se TODOS os botões da tela ficarem escondidos (ex.: fila
única da unidade, fora do horário), aparece a mesma mensagem "Nenhuma opção
disponível neste momento." já usada quando o servidor não retorna nenhum
botão - antes, a grade simplesmente ficaria visualmente vazia sem nenhuma
explicação.

**Testes:** novo `tests/test_regressao_totem_horario_funcionamento.py` (9
casos, relógio simulado via patch de `app.utils.tempo._instante_utc`, mesmo
padrão de `test_regressao_fuso_horario.py`) - fila dentro do horário agora
(visível) e fora (escondido); fila sem `horario_funcionamento` (sempre
visível); botão-menu com todos os descendentes-folha fechados (escondido) e
com pelo menos um aberto (visível); botão-menu sem nenhum descendente-folha
(nunca escondido); presença do embutimento inicial (`STATUS_INICIAL`) e do
`setInterval`/`aplicarStatus` no HTML; endpoint de poll respondendo o mesmo
cálculo do embutido inicial. Suíte completa sem falhas.

**Arquivos alterados:** `app/totem/routes.py`
(`_status_visibilidade_botoes`, rota `status_visibilidade_filas`, embutido em
`tela_raiz`/`submenu`); `app/templates/totem/menu.html` (`data-botao-id` +
script); `app/templates/totem/_layout.html` (`.botao-totem[hidden]`); novo
`tests/test_regressao_totem_horario_funcionamento.py`.

---

## 10.66. ADENDO — "Tempo médio de espera" media só o 1º intervalo da jornada, não cada estágio (mesma classe de bug do 10.62/10.63, agora no relatório irmão)

> Pergunta de arquitetura levantada pelo cliente ao usar o relatório na
> prática: "Tempo médio de espera" mede CADA intervalo de espera da jornada
> de uma senha (geração -> 1ª chamada, realocação -> chamada seguinte, etc.)
> ou só o primeiro? Suspeita do cliente: mesma limitação que
> `tempo_atendimento` tinha antes do 10.62/10.63 - correção que nunca tocou
> este relatório, porque não era o escopo dela na época.

**Investigação confirmou a suspeita, com 3 bugs concretos** em
`tempo_medio_espera` (`app/relatorios/queries.py`, código removido):

1. `eventos_por_senha[senha_id][evento] = timestamp` era um dict simples -
   múltiplos `chamada_senha` pra mesma senha (ex.: re-chamada depois de
   `voltou_aguardar`) se sobrescreviam na iteração, e a query não tinha
   `.order_by()` garantindo qual sobrevivia - resultado NÃO-determinístico.
2. Buscava só os eventos `("gerada", "chamada_senha")` - nunca
   `chamada_nome` (evento de chamada REAL do 2º estágio, ex. Coleta - ver
   `EVENTOS_INICIO_ATENDIMENTO`), nunca `realocado`, nunca `voltou_aguardar`.
   Uma senha Recepção->Coleta nunca tinha o segundo intervalo capturado.
3. Consequência de 1+2: só existia 1 intervalo por senha, ignorando todo o
   resto da jornada depois de qualquer realocação/re-chamada.

**Investigação do item "os outros eventos já têm o snapshot de fila_id/
local_id, ou só `realocado`?" (pedida ANTES de escrever a correção final -
mudaria o escopo se a resposta fosse negativa):** confirmado que **todos os
7 pontos de gravação de `LogAtendimento`** (`app/totem/routes.py`,
`app/atendimento/helpers.py`, `app/middleware/encerramento.py`) já populam
`fila_id`/`local_id` desde o 10.62 - `gerada`, `chamada_senha`,
`chamada_nome`, `voltou_aguardar`, `realocado`, `fim_atendimento`,
`encerramento_automatico_sistema`. A suposição inicial (só `realocado` tinha
o snapshot) estava errada - o docstring de `_segmentos_intermediarios_
realocacao` só *lê* o snapshot de `realocado` porque é tudo que aquela
função específica precisa, não porque os outros eventos não o tenham.
**Consequência: nenhuma migration nova foi necessária, o escopo da correção
não mudou.**

**Correção:** nova `_segmentos_espera_por_senha` (`app/relatorios/
queries.py`) - MESMA técnica de `_segmentos_intermediarios_realocacao`
(10.62: ponteiro sequencial sobre eventos ordenados por `timestamp` via
`eventos.sort()`, não `.order_by()` no SQL - mesma convenção já usada na
função irmã), com os papéis invertidos: eventos que ABREM um intervalo de
ESPERA são `EVENTOS_ABERTURA_ESPERA` (`gerada`/`realocado`/
`voltou_aguardar`); eventos que FECHAM são `EVENTOS_INICIO_ATENDIMENTO`
(`chamada_senha`/`chamada_nome` - mesma constante já usada pra abrir um
segmento de ATENDIMENTO, porque é o MESMO instante: fechar espera = abrir
atendimento). `tempo_medio_espera` ficou uma função fina que só agrupa o
resultado, sem mudar a assinatura/formato de retorno (mesmas chaves
`fila_id`/`unidade_id`/`data`/`tempo_medio_segundos`/`quantidade`) - nenhuma
mudança em `relatorios/routes.py` nem nos templates foi necessária.

**Decisões de produto confirmadas com o cliente antes de implementar:**
- **Cada intervalo pertence à fila de DESTINO (onde o paciente passa a
  esperar), não à origem - INVERSO da convenção de `tempo_atendimento`
  (10.62, que usa origem).** Registrado aqui explicitamente para não parecer
  uma inconsistência não percebida: as duas funções seguem a MESMA regra
  ("usa o snapshot do evento que fecha o segmento") - a diferença de
  origem/destino é consequência de QUAL evento fecha cada tipo de segmento.
  O snapshot de `realocado` é capturado ANTES da senha mudar de fila (por
  isso é origem, `registrar_realocacao`); o snapshot de `chamada_senha`/
  `chamada_nome` é gravado DEPOIS de qualquer realocação anterior já ter
  movido a senha (por isso já reflete a fila de destino).
- **Sem `tipo_fechamento` aqui** (diferente do 10.63) - a fila de destino já
  separa os estágios naturalmente por construção (Recepção/Triagem/Coleta
  têm `fila_id` diferentes), sem o risco de colisão que motivou o 10.63 em
  `tempo_atendimento` (mesmo operador/fila/local em dois tipos de
  fechamento).
- **Dado histórico (sem snapshot) cai no comportamento ANTIGO, por senha**
  (mesma decisão do 10.62): 1 intervalo só, `gerada` -> 1ª `chamada_senha`
  (nunca `chamada_nome`/`realocado`/`voltou_aguardar`, igual ao escopo
  original desta consulta), fila/unidade do estado ATUAL da `Senha` (mesma
  fonte que o código antigo já usava) - só a não-determinismo do bug 1 é
  corrigida também para esses casos (sempre pega a `chamada_senha`
  cronologicamente mais cedo, já que os eventos vêm ordenados).

**Extração de função genérica reaproveitável pelos dois relatórios? Não -
decisão registrada com justificativa:** apesar do algoritmo de pareamento
(ponteiro sequencial) ser idêntico, os DOIS relatórios têm formas de
fallback histórico estruturalmente diferentes (`tempo_atendimento` usa
fila/local `None` + só `unidade_id`; `tempo_medio_espera` usa o estado ATUAL
da `Senha` para fila E unidade) e usam o snapshot de eventos diferentes
(fechamento único vs. múltiplos tipos de fechamento). Forçar uma função
genérica exigiria parâmetros/callbacks pra customizar o formato do
fallback, sem reduzir complexidade de verdade - o padrão já estabelecido
neste arquivo é MANTER funções irmãs separadas com nomes claros
(`_pares_inicio_fim` e `_segmentos_intermediarios_realocacao` já coexistem
assim, mesmo compartilhando bastante estrutura) em vez de abstrair
cross-relatório. `_segmentos_espera_por_senha` é uma função nova e curta
(~15 linhas de lógica de pareamento), duplicar esse trecho custa menos do
que generalizar com parâmetros.

**Achado seguinte, corrigido no MESMO lote (o cliente pediu explicitamente
não deixar para depois):** `app/core/dashboard_queries.py::_tempo_medio_
espera_hoje` (tile "Tempo médio de espera" do dashboard operacional) era uma
implementação INDEPENDENTE (não chamava `tempo_medio_espera()` de
`relatorios/queries.py`) com o MESMO padrão de bug (dict-overwrite, só
`("gerada", "chamada_senha")`) - agregada num único número do dia, não por
fila.

Antes de mudar, o contrato atual foi confirmado (não alterado, por pedido
explícito): a função recebe `(empresa, unidades_visiveis, inicio_hoje,
fim_hoje)` e devolve um único `int` (segundos, arredondado) ou `None` -
consumido por `calcular_kpis()`/`dashboard.html`/`/api/dashboard/kpis` como
um número escalar, nunca uma lista por fila. **Esse formato de saída não
mudou.**

**Correção:** `_tempo_medio_espera_hoje` passou a reaproveitar
`_segmentos_espera_por_senha` (mesma função do relatório completo, acima) -
monta um `Filtros(empresa_id=empresa.id, unidades_permitidas=unidades_
visiveis, unidade_id=None, fila_id=None, ..., data_inicio=inicio_hoje,
data_fim_exclusivo=fim_hoje)` e achata TODOS os intervalos retornados numa
única média, sem distinguir fila/estágio (esse detalhamento continua sendo
exclusivo do relatório completo). Ganha de graça: todos os estágios da
jornada (não só o primeiro), `chamada_nome` além de `chamada_senha`,
resultado determinístico, e o mesmo fallback de dado histórico - sem
duplicar a lógica de pareamento uma terceira vez.

**Testes:** novo `tests/test_regressao_segmentos_espera.py` (6 casos) -
senha com 2 realocações (3 intervalos, cada um na fila de destino correta);
senha sem realocação (1 intervalo, sem regressão); múltiplos
`chamada_senha` na mesma fila via `voltou_aguardar` (2 intervalos somados,
resultado determinístico - prova que o bug do dict-overwrite foi
corrigido); segmento fechado por `chamada_nome` (não só `chamada_senha`);
dado histórico simulado (sem snapshot em nenhum evento) cai no fallback de
1 intervalo, sem erro, fila/unidade do estado atual da `Senha`. Mais novo
`tests/test_regressao_dashboard_tempo_espera_hoje.py` (3 casos) - senha
realocada hoje (a média do tile passa a incluir os 2 intervalos, não só o
1º); nenhuma senha com intervalo fechado hoje (`None`, não `0`); dado
histórico (fallback, sem erro). Suíte completa (67 arquivos) sem falhas.

**Arquivos alterados:** `app/relatorios/queries.py`
(`EVENTOS_ABERTURA_ESPERA`, `_segmentos_espera_por_senha`,
`tempo_medio_espera` reescrita); `app/core/dashboard_queries.py`
(`_tempo_medio_espera_hoje` reescrita, reaproveitando `_segmentos_espera_
por_senha`); novos `tests/test_regressao_segmentos_espera.py` e
`tests/test_regressao_dashboard_tempo_espera_hoje.py`.

---

## 10.67. ADENDO — Exportação de relatórios em PDF (complementar ao CSV/Excel existente)

> Pedido: além da exportação em CSV/Excel já existente (seção 7, via
> `pandas`/`openpyxl`), adicionar exportação em PDF para os relatórios do
> perfil Gerencial/Admin - uso principal é impressão/arquivamento e anexo em
> prestação de contas. O pedido original trazia três premissas que a
> investigação (antes de implementar, não presumida) derrubou ou revisou -
> registradas aqui em detalhe porque mudaram a arquitetura da correção.

**1. WeasyPrint (biblioteca original do pedido) não funciona nesta máquina de
desenvolvimento só com `pip install` - testado, não presumido:**
```
pip install weasyprint   →  sucesso (baixa o wheel)
from weasyprint import HTML  →  falha
OSError: cannot load library 'libgobject-2.0-0': error 0x7e.
```
WeasyPrint depende de bibliotecas nativas do ecossistema GTK (Pango/Cairo/
GObject) carregadas via `dlopen` em tempo de EXECUÇÃO, não de instalação -
`pip install -r requirements.txt` completa "com sucesso" e engana, o import
só quebra na hora de gerar um PDF de verdade. Isso quebraria o "só `pip
install` e roda" que o README documenta hoje. **Trocado por Chromium
headless via Playwright** (`playwright install chromium` - baixa o
navegador consigo mesmo, sem depender de instalador de sistema separado em
nenhum SO) - testado e confirmado gerando um PDF de verdade nesta mesma
máquina sem nenhum passo manual extra além desse.

**2. Nenhum dos 4 relatórios exportáveis tem `nome_paciente` - a parte do
pedido sobre "PDF sem nome do paciente por padrão, checkbox pra incluir com
permissão explícita, log de quem viu o nome" foi REMOVIDA do escopo, não
implementada:** `tempo_espera`, `tempo_atendimento`, `volume`,
`ranking-operadores` são todos agregados (por fila/unidade/dia/operador -
médias, contagens), nenhum é uma listagem por senha. `nome_paciente` só
aparece em `ultimas_chamadas()` (dashboard operacional, sem exportação
nenhuma). Além disso, `Perfil.permissoes` (coluna JSON que serviria de base
pra uma permissão granular) existe no schema mas nunca é lida em nenhum
lugar do código - não havia infraestrutura de permissão fina pra
reaproveitar, só o RBAC grosso `gerencial`/`admin` que já protege todo o
blueprint. **Decisão confirmada com o cliente:** não construir essa parte
agora (nada pra ela agir hoje); se um relatório por senha/paciente for
criado no futuro, essa decisão volta à mesa com um alvo real.

**3. Playwright chamado direto dentro do processo Flask emite um aviso real
de threading não-cooperativa com o eventlet - testado antes de decidir a
arquitetura final:**
```
1 RLock(s) were not greened, to fix this error make sure you run
eventlet.monkey_patch() before importing any other modules.
```
`run.py` chama `eventlet.monkey_patch()` globalmente (Gunicorn `--worker-
class eventlet` em produção, README) - um único worker eventlet processa
MUITAS requisições concorrentes numa única thread do SO; se a geração de
PDF bloquear essa thread de verdade (não de forma cooperativa), todas as
outras requisições daquele worker ficariam paradas enquanto um PDF é
gerado. **Corrigido isolando a geração num SUBPROCESS de verdade**
(`app/relatorios/_pdf_worker.py`, chamado via `subprocess.run()` em
`app/relatorios/pdf.py`) - o processo filho roda um Python novo, sem o
monkey-patch do pai, então o Playwright roda sem nenhuma interferência; o
processo PAI só espera o resultado via `subprocess.run()`, que o eventlet
já sabe tornar cooperativo (não bloqueia as outras requisições enquanto
espera). `_pdf_worker.py` é deliberadamente um script standalone (rodado
como arquivo, não como `-m app.relatorios._pdf_worker`) - não passa pela
árvore de import de `app/relatorios/__init__.py` (que importaria toda a
árvore de rotas/queries à toa só pra converter HTML em PDF).

**Geração via HTML → PDF, reaproveitando o MESMO template da tela, como
pedido originalmente:** os 4 templates de relatório
(`tempo_espera.html`/`tempo_atendimento.html`/`volume.html`/`ranking.html`)
passaram de `{% extends "relatorios/_layout.html" %}` fixo para `{% extends
layout_relatorio %}` - uma variável de contexto com default
`"relatorios/_layout.html"` (`@relatorios_bp.context_processor`, `app/
relatorios/__init__.py`) que a exportação em PDF sobrescreve explicitamente
para `"relatorios/_layout_impressao.html"` (novo - sem `<nav>`/dark-mode/
formulário de filtros interativo, cabeçalho com empresa/filtros aplicados
por extenso, rodapé com data/hora de geração). O MESMO `{% block conteudo
%}` (a tabela em si) é reaproveitado nos dois formatos - um ajuste visual
futuro na tabela já reflete na tela E no PDF automaticamente, sem duas
fontes de verdade. `{{ form_filtros(...) }}` (formulário + links de
exportar) é pulado via `{% if not modo_impressao %}` (outra variável do
mesmo context_processor).

`relatorios/print.css` (novo, incluído INLINE via `{% include %}` dentro de
`<style>` em `_layout_impressao.html`, não como `<link>` externo - o HTML
entregue ao worker precisa ser 100% auto-contido, sem recurso externo a
buscar, já que o worker roda isolado sem servidor por trás) - cores LITERAIS
(não `var(--gf-*)`), mesma decisão já tomada pro cupom impresso do totem
(`totem/senha_gerada.html`): um PDF impresso/arquivado é sempre "papel
branco, tinta preta", independente do tema de quem gerou o relatório.
Paginação (`Página X de Y`) via `@page { @bottom-right { content: counter
(page) ...} }` - testado antes de assumir que funcionaria: ao contrário do
que se poderia temer (Chromium tem suporte historicamente incompleto de CSS
Paged Media), o motor de impressão do Chromium via `page.pdf()`
(Playwright) renderiza esses counters corretamente, confirmado extraindo o
texto de um PDF de teste multi-página com `pypdf`.

**Log de auditoria da exportação (novo, não existia para CSV/Excel) - passa
a valer pros TRÊS formatos, não só PDF:** nova tabela
`logs_exportacao_relatorio` (`app/models/log_exportacao.py` +
migration `b3d6f8a1c4e7`) - usuário, empresa, timestamp, tipo de relatório,
formato do arquivo, filtros aplicados (unidade/fila/operador/período). Sem
o campo de nome do paciente incluído (item 2 acima, removido do escopo).
Motivado pela mesma lente de LGPD já usada no restante do projeto (README,
seção "Dado sensível"): registrar quem exportou o quê é parte de conseguir
responder "quem teve acesso a este dado" se algum dia for preciso - não é
redundante com `LogAtendimento` (que audita o ATENDIMENTO em si, não o
ACESSO/exportação de dados por um usuário do admin).

**Dispatch de exportação unificado numa função só, não duplicado 4x:**
`_exportar_relatorio()` (`app/relatorios/routes.py`) - as 4 rotas tinham o
MESMO bloco `if request.args.get("formato") in ("csv", "xlsx"): ...`
duplicado; cada uma precisaria ganhar o mesmo trecho novo de PDF + log de
auditoria. Diferente do caso de `_segmentos_espera_por_senha`/`_segmentos_
intermediarios_realocacao` (`queries.py`, adendo 10.66 - mantidas
separadas de propósito, fallbacks de dado histórico divergentes), aqui o
dispatch é IDÊNTICO entre os 4 relatórios - função única, não duplicação.

**Filtros e período no PDF idênticos aos já existentes na tela (seção 7):**
nenhum filtro novo introduzido. `filtros_legiveis()` (`app/relatorios/
helpers.py`) resolve IDs pra nomes (unidade/fila/operador) e formata o
período por extenso, pro arquivo ser autoexplicativo fora do sistema.

**Falha na geração do PDF é recuperável, não um 500 cru:** `renderizar_pdf()`
levanta `FalhaGeracaoPdf` (timeout do subprocess ou saída de erro) -
`_exportar_relatorio()` captura, loga e devolve um flash de erro + redirect
de volta pro relatório (sugerindo CSV/Excel como alternativa), em vez de
quebrar a requisição inteira.

> **Achado adicional, na revisão antes da aprovação (não presumido) - o
> Chromium deixava pastas de perfil temporárias pra trás no Windows:**
> `p.chromium.launch()` sozinho usa um perfil interno do próprio Chromium
> (pasta `scoped_dir*` dentro do temp do SO) - gerando várias dezenas de PDFs
> em sequência nesta máquina e inspecionando o temp depois, as pastas
> ficavam pra trás mesmo após `browser.close()` (vazias - não é vazamento de
> espaço em disco de verdade, mas lixo acumulando indefinidamente numa
> máquina de produção rodando por meses, sem nenhuma limpeza automática).
> **Corrigido** (`app/relatorios/_pdf_worker.py`) usando
> `launch_persistent_context(perfil_dir)` com um `tempfile.mkdtemp()`
> PRÓPRIO, removido explicitamente em `finally` (`shutil.rmtree`, cobre tanto
> o caminho de sucesso quanto exceção) - testado de novo depois da correção,
> zero resíduo. O worker nunca escreve o HTML/PDF em disco em nenhum ponto
> (HTML chega via stdin, PDF sai via stdout, `subprocess.run(...,
> capture_output=True)`) - só o perfil interno do Chromium tocava disco, e
> agora tem ciclo de vida garantido.

> **Dois achados adicionais, na revisão SEGUINTE antes da aprovação final -
> um deles bloqueante por bater num padrão de bug já visto 3x neste
> projeto:**
>
> **1. BLOQUEANTE - `gerado_em=datetime.utcnow()` no rodapé do PDF era a
> MESMA classe de bug já corrigida pelos adendos 10.37/10.39/10.54** (hora/
> data do servidor em UTC, em vez do fuso da clínica resolvido no código,
> `app/utils/tempo.py::agora_na_clinica()`). Um PDF impresso/arquivado
> mostrando "Gerado em 23:47" quando na clínica são 20:47 é exatamente o
> tipo de coisa que um usuário nota e reporta - e o tipo de regressão fácil
> de deixar passar porque `datetime.utcnow()` "funciona" nos testes
> automatizados (que não fixam fuso por padrão). **Corrigido:** troca direta
> por `agora_na_clinica()` (já importada/usada em todo o resto do projeto
> pra esse exato propósito - nenhuma lógica de fuso nova). **Testado com o
> relógio simulado** (`patch("app.utils.tempo._instante_utc", ...)`, mesmo
> padrão de `test_regressao_fuso_horario.py`) num instante UTC onde a hora
> de Brasília é visivelmente diferente (23:47 UTC = 20:47 Brasília) -
> confirma "20:47" no rodapé do PDF gerado de verdade, não "23:47".
>
> **2. `registrar_exportacao()` rodava ANTES de `renderizar_pdf()` ter
> sucesso** - se a geração falhasse (`FalhaGeracaoPdf`), o log de auditoria
> já tinha sido gravado como se a exportação tivesse ocorrido, mesmo o
> usuário só recebendo um flash de erro. Pra um log cujo propósito é
> auditoria LGPD ("quem teve acesso a este dado"), registrar uma tentativa
> falha como acesso real é impreciso. **Corrigido:** `registrar_exportacao()`
> movida pra DEPOIS do sucesso nos 3 formatos (CSV/Excel também, por
> consistência, não só PDF) - no caminho de PDF, só roda depois de
> `renderizar_pdf()` retornar sem exceção. **Testado** com
> `renderizar_pdf` mockado pra lançar `FalhaGeracaoPdf`: confirma que a
> contagem de `logs_exportacao_relatorio` não muda quando a geração falha.

**Fora de escopo nesta versão (candidatos a adendo futuro, não implementados
agora):** relatório agendado/recorrente (envio automático por e-mail);
geração de PDF combinando mais de um tipo de relatório num único arquivo;
a permissão granular de nome_paciente (item 2 acima).

**Testes:** novo `tests/test_regressao_exportacao_pdf.py` (6 casos, PDF
gerado de PONTA A PONTA - não mockado, sobe o subprocess/Chromium de
verdade e extrai o texto de volta com `pypdf`) - PDF contém os filtros
aplicados no cabeçalho E o dado real da tabela (mesmo template da tela); log
de auditoria gravado corretamente para os três formatos (CSV, Excel, PDF);
RBAC idêntico ao das rotas de exportação existentes (403 para perfil sem
acesso a relatórios, mesmo pedindo `formato=pdf`); rodapé usa o fuso da
clínica, não UTC do servidor (relógio simulado, item 1 da revisão acima);
falha na geração do PDF não grava log de auditoria (`renderizar_pdf`
mockado, item 2 da revisão acima). `tests/test_regressao_
exportacao_relatorios.py` ajustado - `formato=pdf` deixou de ser "formato
desconhecido, ignorado" (comportamento antigo testado ali) e virou um
formato real; o caso de "formato inválido cai pro HTML" passou a usar
`formato=json` (verdadeiramente desconhecido) pra continuar cobrindo esse
comportamento. Suíte completa (68 arquivos) sem falhas.

**Arquivos alterados:** `requirements.txt` (`playwright`, `pypdf` - este
último só para a suíte de testes); `.github/workflows/tests.yml` (`playwright
install --with-deps chromium`); `README.md` (passo de instalação do Chromium,
dev local E produção Ubuntu); `app/relatorios/pdf.py` (novo),
`app/relatorios/_pdf_worker.py` (novo); `app/templates/relatorios/print.css`
(novo), `app/templates/relatorios/_layout_impressao.html` (novo);
`app/models/log_exportacao.py` (novo), migration `b3d6f8a1c4e7`;
`app/relatorios/__init__.py` (`context_processor`); `app/relatorios/
helpers.py` (`filtros_legiveis`, `registrar_exportacao`); `app/relatorios/
routes.py` (`_exportar_relatorio`, as 4 rotas); `app/templates/relatorios/
_macros.html` (link "PDF"); os 4 templates de relatório (`{% extends
layout_relatorio %}` + `{% if not modo_impressao %}`); novo
`tests/test_regressao_exportacao_pdf.py`; `tests/test_regressao_exportacao_
relatorios.py` ajustado.

---

## 10.68. ADENDO — Dois achados independentes na exportação de PDF (adendo 10.67): eventlet trava no Windows local + cache do Chromium por usuário em produção

> Dois problemas descobertos em sessões de debug separadas, ambos
> decorrentes do mesmo recurso novo (exportação de PDF, 10.67), com causas e
> correções independentes - registrados juntos aqui por serem do mesmo
> adendo pai, não por serem a mesma causa.

**Achado 1 - `eventlet.monkey_patch()` incondicional travava o servidor de
DESENVOLVIMENTO LOCAL inteiro no Windows ao clicar em "Exportar PDF" (painel
de TV parava de atualizar via WebSocket, novas abas travavam):** causa raiz
confirmada por teste - `eventlet.monkey_patch()` torna `subprocess.run()`
cooperativo com o hub de greenthreads do eventlet no LINUX, mas não no
Windows (limitação de plataforma do próprio eventlet, não um bug deste
projeto). O worker de PDF (`app/relatorios/_pdf_worker.py`, adendo 10.67)
roda em `subprocess.run()` a partir do processo Flask - sem a cooperação do
eventlet nesse SO, essa chamada bloqueia de verdade o único hub do servidor
de desenvolvimento (`socketio.run()`, `run.py`), travando toda e qualquer
outra requisição/WebSocket enquanto o PDF é gerado. Em produção (Gunicorn +
worker eventlet em Linux, README) isso nunca foi um problema - a limitação é
específica do Windows.

**Correção:** `eventlet.monkey_patch()` (`run.py`) e o `async_mode` do
`SocketIO` (`app/extensions.py`) passam a ser condicionados por
`sys.platform` - `"eventlet"`/monkey-patch ativo em produção/Linux (sem
mudança de comportamento lá), `"threading"`/monkey-patch pulado no Windows
(dev local) - `"threading"` usa threads reais do SO, que não sofrem dessa
limitação de cooperação. **Validado**: painel de TV confirmado "CONECTADO"
via WebSocket em tempo real no Windows depois da mudança, e a exportação de
PDF parou de travar o servidor local.

**Achado 2 - cache do Chromium é por usuário, `sudo playwright install`
sozinho não basta pro Gunicorn (`www-data`) enxergar o navegador em
PRODUÇÃO:** achado numa instalação real em produção (servidor
`srv-painelsenha`), seguindo o passo de instalação do Chromium documentado
pelo adendo 10.67: `sudo venv/bin/playwright install --with-deps chromium`
(o comando original) instala as dependências de sistema (`apt`, precisa de
root) E baixa o binário do Chromium - mas o binário vai pro cache do usuário
que RODA o comando (`~/.cache/ms-playwright`, convenção padrão do
Playwright). Rodado via `sudo` sem trocar de usuário, esse "usuário que
roda" é `root` (`/root/.cache/ms-playwright`) - o Gunicorn de produção roda
como `www-data` (`gestfila.service`, passo 9 do README), um usuário
DIFERENTE, com seu próprio cache vazio. Resultado: o `apt install`
funcionou, mas a primeira exportação de PDF em produção falhou com
"Executable doesn't exist" - um erro que só apareceu na hora de usar a
feature, não durante a instalação, exatamente o tipo de gap fácil de não
notar seguindo o guia literal.

**Correção:** passo de instalação do Chromium (README, seção "Instalação em
produção") dividido em DOIS comandos com usuários diferentes - `sudo
venv/bin/playwright install --with-deps chromium` (root, só as dependências
de sistema + o binário no cache de root, descartável) e `sudo mkdir -p
/var/www/.cache && sudo chown www-data:www-data /var/www/.cache && sudo -u
www-data venv/bin/playwright install chromium` (o binário no cache que o
Gunicorn (`www-data`) de fato vai usar em runtime) - mais um comando de
verificação (`sudo -u www-data venv/bin/python3 -c "..."`) que sobe e fecha
um Chromium de verdade como `www-data`, sem depender do Gunicorn/da tela de
relatórios estarem no ar pra confirmar que o passo funcionou.

**Confirmado em servidor de produção real** (mesmo padrão dos achados de
instalação já registrados, 10.59/10.61) - reproduzido no servidor
`srv-painelsenha`: `journalctl -u gestfila` mostrou exatamente o erro
previsto, `Executable doesn't exist at /var/www/.cache/ms-playwright/...`,
confirmando que `sudo venv/bin/playwright install --with-deps chromium`
sozinho (rodando como root) de fato deixa o binário fora do alcance do
Gunicorn (`www-data`). A correção (`sudo -u www-data venv/bin/playwright
install chromium`) foi aplicada nesse mesmo servidor e validada com sucesso
depois: PDF gerado nos 4 relatórios, e o RBAC (403 pro perfil `atendente`)
confirmado em produção também.

**Testes:** suíte completa (68 arquivos) reexecutada sem falhas depois do
Achado 1 - CI (Linux) não muda de comportamento (`_ASYNC_MODE` continua
`"eventlet"` lá), a suíte de testes nunca importa `run.py`/passa pelo
monkey-patch de qualquer forma (`create_app("testing")` direto). Achado 1
validado manualmente (não por teste automatizado - é comportamento de
WebSocket/servidor de desenvolvimento real, fora do escopo dos scripts de
regressão deste projeto); Achado 2 validado em produção real (ver acima).

**Arquivos alterados:** `app/extensions.py`, `run.py` (Achado 1);
`README.md` (passo 5.1 da seção "Instalação em produção", Achado 2).

---

## 10.69. ADENDO — Fila sem identificação de unidade nos relatórios/dashboards + select de Fila não filtrava pela Unidade escolhida

> Bug real relatado em produção, com print: em `/relatorios/ranking-
> operadores`, o select "Fila" listava várias opções com o MESMO texto
> ("Coleta" 5x, "Deve Material" 8x+), sem indicar a unidade de cada uma. Com a
> Unidade "Matriz" escolhida, o select continuava listando filas de TODAS as
> unidades. Investigação (FASE 1) relatada e aprovada antes de implementar.

> **Nota cruzada (10.70):** `rotulos_filas()` foi movida para
> `app/utils/rotulos.py` (recebe `empresa_id`) e passou a alimentar também as
> telas operacionais e os cadastros admin; as referências a
> `app/relatorios/helpers.py` abaixo descrevem o estado deste adendo.

**Achados da investigação:**

1. **Select "Fila"** (`relatorios/_macros.html::form_filtros`, compartilhado
   pelas 4 telas: tempo_espera, tempo_atendimento, volume, ranking): lista
   plana de `Fila.nome`, sem `<optgroup>`, prefixo nem `data-unidade-id`.
   Alimentado por `opcoes_filtro()` (`app/relatorios/helpers.py`), que só
   restringe às unidades VISÍVEIS ao usuário, não à Unidade escolhida no
   filtro.
2. **Nenhum filtro por unidade, nem no cliente nem no servidor** (mesma
   classe de bug do 10.53/10.64): não havia JS nas telas de relatório, e
   `resolver_filtros()` validava `fila_id` só contra empresa + unidades
   visíveis, não contra `unidade_id`. Fila da Laranjeiras + Unidade Matriz
   aplicava os dois `WHERE` juntos e o relatório saía **vazio, sem aviso**.
   O isolamento multi-tenant já estava correto (fila de outra empresa já
   virava `None`).
3. **Banco:** o MySQL local (22 filas, collation `utf8mb4_0900_ai_ci`) tinha
   só casos do grupo (a) - nome repetido entre unidades diferentes ("Coleta"
   em 3 unidades, "Deve Material"/"Entrega de Resultado" em 2) - e nenhum do
   grupo (b) - nome repetido DENTRO da mesma unidade. O banco local não
   representa produção (o print mostra mais repetições); a consulta em
   produção ficou com o cliente, que vai tratar os casos reais do grupo (b)
   como DADO (renomear/desativar filas), não como código. **Achado extra:**
   nenhuma unidade tinha `Unidade.sigla` cadastrada, e o fallback de 3 letras
   do 10.22 colide - "Anápolis - Centro Médico" e "Anápolis - Laborátorio"
   viram ambas "[ANÁ]". O cliente vai cadastrar a sigla de todas as
   unidades, mas o código não depende disso (desempate abaixo).
4. **Pontos de exibição afetados:** select (4 telas); coluna Fila da tabela e
   do CSV/Excel de tempo_atendimento (sem coluna Unidade - a coluna Local às
   vezes ajudava via `identificador_admin`, mas sai "—" em Repasse
   histórico); cabeçalho do PDF (`filtros_legiveis`, "Fila: Coleta", nas 4
   telas); `relatorios/dashboard.html` ("Senhas aguardando por fila" e
   "Últimas chamadas"); `core/dashboard.html` ("Últimos atendimentos"; a
   lista de filas já mostrava "Coleta (Matriz)", só num formato diferente).
   Tabela e CSV/Excel de tempo_espera/volume já tinham coluna Unidade;
   ranking não tem coluna Fila.
5. **Selects de Unidade e Operador:** Unidade escolhe uma única unidade e os
   nomes são distintos (fora do item 6 do 10.22, sem problema). Operador
   também não filtra pela unidade - registrado como pendência na seção 11,
   fora do escopo deste adendo.

**Decisões (confirmadas com o cliente):**

- **Fonte ÚNICA do rótulo:** `rotulos_filas(empresa)` (`app/relatorios/
  helpers.py`) devolve `{fila_id: "[SIGLA] Nome"}` (`Unidade.sigla_exibicao`,
  10.22). É usada por `mapas_nomes()` (que deixou de ser uma tupla de 4 e
  virou o `NamedTuple` `MapasNomes`: `filas` = rótulo, `filas_nome` = nome
  puro, `filas_unidade` = unidade da fila), pelo select (`opcoes_filtro()`),
  por `filtros_legiveis()` e pelos dois dashboards. O `filas_nomes` local que
  `relatorios/routes.py::dashboard` montava à parte foi eliminado. Nenhum
  template concatena sigla e nome.
- **Desempate residual = regra do 10.32 item 3:** se, mesmo com o prefixo,
  sobrar colisão case-insensitive E accent-insensitive (mesma noção de
  "igual" da collation `utf8mb4_0900_ai_ci`, via `_chave_colisao`), " (id)" é
  anexado a TODOS os membros do grupo, nunca só aos "duplicados" - ex.:
  "[ANÁ] Coleta (14)" e "[ANÁ] Coleta (15)". Cobre os dois cenários: fallback
  de sigla colidindo e nome repetido na mesma unidade. **Se esse sufixo
  aparecer em produção, é o comportamento esperado, não um bug** - some
  sozinho quando a sigla for cadastrada ou a fila renomeada. Siglas de
  botões-folha (10.47) foram descartadas como diferenciador.
- **Desempate calculado sobre a EMPRESA INTEIRA** (inclusive filas inativas e
  de unidades que o usuário não vê), nunca sobre o subconjunto da página: o
  mesmo id gera sempre o mesmo rótulo em qualquer tela/perfil (um gerencial
  restrito a uma das unidades "ANÁ" vê o mesmo "(id)" que o admin).
  **Filas inativas entram no desempate de propósito** (decisão confirmada com
  o cliente na revisão; a consulta de `rotulos_filas` nunca filtrou por
  `ativo`): o rótulo de uma fila fica estável - não ganha nem perde o
  "(id)" quando outra fila do grupo é ativada/desativada - e relatórios de
  períodos antigos, onde a fila hoje inativa ainda aparece, continuam
  distinguíveis.
- **Select de Fila:** `<optgroup label="<nome completo da unidade>">` (via
  `groupby('unidade.nome')` do Jinja, mesmo padrão de `admin/filas/form.html`
  e `admin/totem_botoes/form.html`) + option com o rótulo + `data-unidade-id`
  (no option e no optgroup). Um script no próprio macro
  (`filtrarFilasPorUnidade`) esconde (`hidden`, nunca remove do DOM) as filas
  de outras unidades no load e a cada `change` da Unidade; "Todas" mostra
  tudo; uma seleção que sai de vista volta para "Todas". O optgroup some
  quando não sobra nenhuma opção visível dentro dele - decidido pelas
  opções, não pelo `data-unidade-id` do grupo, para continuar certo mesmo se
  duas unidades homônimas caírem no mesmo grupo do `groupby`. **`hidden`
  confirmado antes de aplicar:** nenhum `display:` de autor em
  `option`/`optgroup` (`relatorios/_layout.html` só estiliza `.filtros
  select`; `gestfila.css` só define background/color) - mesmo caso do 10.64.
  Validado também num Chromium headless (Playwright) com a página
  renderizada, não só por inspeção do HTML.
- **Servidor:** `resolver_filtros()` descarta (vira `None`) um `fila_id` de
  outra unidade que a escolhida - mesmo padrão silencioso dos demais filtros.
  O relatório passa a sair com a unidade inteira, em vez de vazio.
- **CSV/Excel:** coluna Fila com o nome PURO + coluna "unidade" separada (mais
  fácil de filtrar numa planilha do que um prefixo dentro da célula).
  tempo_espera/volume já eram assim; tempo_atendimento ganhou a coluna
  "unidade" (unidade da fila; no Repasse histórico sem snapshot de fila,
  10.62, a unidade da senha - `tempo_atendimento()` passou a expor esse
  `unidade_id`, que já fazia parte da chave interna, sem mudar nenhum
  cálculo).
- **Telas e PDF:** "[SIGLA] Nome" na coluna Fila das tabelas que a têm
  (inclusive tempo_espera/volume, para o mesmo id ter o mesmo rótulo em toda
  tela), nos dois dashboards (a lista de filas do `core/dashboard` trocou
  "Coleta (Matriz)" por "[MAT] Coleta") e no cabeçalho do PDF ("Fila: [MAT]
  Coleta").
- **Fora de escopo, sem alteração:** nenhuma regra de cálculo dos relatórios;
  telas operacionais/admin que já usavam "[SIGLA] Nome" montado no próprio
  template (Iniciar Turno, Redirecionar, cadastros de Fila/Botão do totem,
  10.22) continuam como estavam - não usam o desempate por id.

**Limitação conhecida - `hidden` em `<option>`/`<optgroup>` não funciona no
Safari (macOS/iOS):** o WebKit ignora o atributo nessas tags, então lá todas
as filas continuam visíveis no select mesmo com uma Unidade escolhida. Vale
igualmente para o 10.64 ("Botão pai"/"Fila" em `admin/totem_botoes/form.html`,
mesma técnica). **Não** vale para o 10.53: lá o filtro esconde `<label>`s de
um checklist de checkboxes (`admin/locais/form.html`), e `hidden` em
`<label>` funciona normalmente no Safari. **Mitigação:** o filtro no cliente
é só UX - o servidor nunca confia nele (`resolver_filtros` descarta um
`fila_id` de outra unidade aqui; `_validar_totem_botao` rejeita no 10.64),
então o pior caso no Safari é ver opções a mais, nunca um resultado errado
ou dado de outra unidade/tenant. Não tratado
agora (os relatórios são usados em desktop/Chrome); se virar problema, a
alternativa é remover/reinserir as `<option>` no DOM em vez de escondê-las.

**Testes:** novo `tests/test_regressao_relatorios_fila_unidade.py` (16
verificações). Cenário: quatro unidades com uma "Coleta" cada, duas delas sem
sigla com fallback colidindo ("Anápolis - X"/"Anápolis - Y"), mais duas filas
na mesma unidade diferindo só em caixa/acento, e um par "Deve Material" na
mesma unidade com uma das filas INATIVA. Verifica: rótulos e " (id)" em
todos os membros; a fila inativa entra no desempate e o rótulo da ativa não
muda ao ativar/desativar a outra; `<optgroup>`/rótulo/`data-unidade-id`/script nas 4 telas;
`fila_id` de outra unidade descartado (relatório não sai vazio) e de outra
empresa ignorado, sem vazar dado; o mesmo rótulo no select, nas tabelas de
volume e tempo_atendimento, nos dois dashboards e no PDF gerado de ponta a
ponta (tabela + "Fila: ..." de `filtros_legiveis`); gerencial restrito a uma
unidade recebe o mesmo rótulo do admin; CSV de tempo_atendimento e Excel de
volume com nome puro + coluna "unidade". Nenhum teste existente precisou de
ajuste (os que conferiam o nome da fila em CSV/Excel continuam valendo,
porque o CSV manteve o nome puro; os de PDF/tela usam substring). Suíte
completa (69 arquivos) sem falhas.

**Arquivos alterados:** `app/relatorios/helpers.py` (`rotulos_filas`,
`_chave_colisao`, `MapasNomes`/`mapas_nomes`, `opcoes_filtro`,
`resolver_filtros`, `filtros_legiveis`); `app/relatorios/routes.py` (as 4
rotas + dashboard, sem o `filas_nomes` local); `app/relatorios/queries.py`
(`tempo_atendimento` expõe `unidade_id`); `app/core/routes.py` (dashboard e
`_ultimos_atendimentos_enriquecidos`); `app/templates/relatorios/
_macros.html` (optgroup + script); `app/templates/relatorios/dashboard.html`;
`app/templates/core/dashboard.html`; novo `tests/test_regressao_relatorios_
fila_unidade.py`. Índice 10.0 completado (faltavam as linhas 10.47-10.68).

---

## 10.70. ADENDO — Rótulo de fila "[SIGLA] Nome" da função única do 10.69 nas telas operacionais e cadastros admin

> Continuação do 10.69: os pontos FORA dos relatórios que montavam "[SIGLA]
> Nome" de fila por conta própria (no template ou no JS) passam a usar a
> mesma função, com o mesmo desempate " (id)". Investigação (FASE 1)
> relatada e aprovada antes de implementar.

> **NOTA DE DEPLOY:** cadastrar `Unidade.sigla` em TODAS as unidades ANTES de
> liberar o uso deste adendo em produção - **pela tela de Unidades** (campo
> "Sigla", adendo 10.71; a listagem marca as siglas automáticas e as que
> colidem). Sem isso, unidades cujo fallback de 3 letras colide (ex.: as duas
> "Anápolis - ..." -> "[ANÁ]") fazem aparecer " (id)" nas telas operacionais
> (Iniciar Turno, Redirecionar, tela por Local) - comportamento correto do
> código (aceito pelo cliente), mas desnecessário se as siglas estiverem
> cadastradas. Os rótulos refletem a mudança na próxima request, sem
> reiniciar nada. (Antes do 10.71 a tela não tinha o campo e isto só era
> possível via SQL.)

**Achados da investigação:**

1. **Pontos que montavam o rótulo de fila (escopo):**
   - A1 - Iniciar Turno: `atendimento/turno.py` mandava `unidade`/
     `unidade_sigla` no JSON embutido e o JS de `iniciar_turno.html`
     concatenava em dois lugares (tabela "Filas do turno", com "[SIG] nome
     (Unidade)", e o `exibir` do autocomplete).
   - A2 - Redirecionar: `atendimento/_redirecionar_busca.html` concatenava
     `nome_exibicao` no Jinja (renderizado por `painel_fila` e
     `painel_local`).
   - A3 - Cadastro de Botão do totem: `<option>` do select "Fila"
     concatenado no template.
2. **"[SIGLA]" que NÃO é de fila** (fora de escopo, sem alteração): selects
   de Totem/TV do cadastro de Fila (`admin/filas/form.html` - o formulário de
   Fila não lista filas), selects de Unidade (`admin/_layout.html`,
   `core/dashboard.html`) e a sugestão de `identificador_admin` do Local.
3. **Nome de fila sem prefixo em telas que misturam unidades**: entraram no
   escopo os checklists "Nome (Unidade)" de `admin/locais/form.html` e
   `admin/tvs/form.html` e a tela por Local do operador (`painel_local.html`
   + `_serializar_*_local`). Ficaram de fora (decisão do cliente) as
   listagens que já têm coluna Unidade separada (`admin/filas/listar.html`,
   `atendimento/listar_filas.html`) e os flashes "realocada para 'X'". Telas
   de paciente (cupom do totem, painel de TV) e telas de uma fila/unidade só
   (`painel_fila`, `alocar_fila`, `locais/rodizio.html`) nunca entram.
4. **Tela por Local - confirmado antes de mexer que o rótulo só chega a
   telas de OPERADOR:** `_serializar_espera_local`/`_serializar_atendida_
   local`/`_serializar_senha_atual_local` (`atendimento/chamada.py`) só são
   chamados por `dados_local` (rota `/<setor>/local/<id>/dados`, protegida por
   `exigir_acesso_setor`). O WebSocket NÃO carrega nome de fila nenhum:
   `fila_atualizada` leva só `{fila_id}` (`atendimento/helpers.py::
   emitir_atualizacao_fila`) e a tela refaz o GET em `/dados`; o evento
   `chamada` que alimenta a TV usa `_payload_chamada` (`tipo`/`local`/
   `senha` ou `nome_paciente`/`local_audio_slug`/`cor`), sem fila - não
   mudou. O cupom do totem (`totem/senha_gerada.html`, `fila.nome`) também
   não mudou.

**Decisões e implementação:**

- **`rotulos_filas()` mudou de casa:** `app/relatorios/helpers.py` ->
  `app/utils/rotulos.py` (junto com `_chave_colisao`), sem re-export no
  módulo antigo - atendimento/admin não dependem mais do pacote de
  relatórios. Assinatura passou a receber `empresa_id` (os call-sites de
  atendimento não têm o objeto `Empresa` à mão). Regra inalterada: empresa
  inteira, inclusive inativas, " (id)" em todos os membros do grupo
  colidente.
- **`rotulo_fila(fila)`** (mesmo módulo): atalho para UMA fila, registrado
  como global Jinja em `create_app` e usado também pelos serializadores
  Python. Memoiza `rotulos_filas()` por request em `g` (uma consulta por
  empresa por request, não uma por fila; uma fila criada depois do cache
  montado força recálculo). Evita repassar um mapa por cada um dos ~15
  `render_template` envolvidos (formulários de botão/local/TV em
  novo/editar/erro, `painel_fila`, `painel_local`, turno). Os relatórios
  continuam usando o mapa `rotulos_filas()` direto (já o tinham em
  `opcoes`/`mapas_nomes`).
- **Escopo do cache de `rotulo_fila()` (confirmado na revisão antes do
  commit):** o cache só existe DENTRO de uma request
  (`has_request_context()`). Fora de request (flask shell, scripts, seeds)
  não há cache: `g` pertence ao app context, não à request, e num shell/script
  o app context pode durar a sessão inteira - um cache ali devolveria rótulo
  velho depois de renomear uma fila ou cadastrar uma sigla (achado desta
  revisão, corrigido; antes funcionava sem erro, mas com esse risco).
  Handlers de Socket.IO rodam com request context próprio por evento
  (Flask-SocketIO), então o cache vale só para aquele evento. **Por que o
  cache por request é seguro hoje:** nenhuma rota altera uma fila (ou a sigla
  de uma unidade) e renderiza um rótulo na MESMA request - criar, editar,
  ativar/desativar e excluir fila (`app/admin/filas.py`) terminam em
  `redirect`; o re-render com erro de validação acontece ANTES de qualquer
  alteração e `admin/filas/form.html` nem usa `rotulo_fila`; o cadastro de
  Unidade também redireciona após salvar e não exibe rótulo de fila. Se uma
  rota futura alterar fila/sigla e renderizar rótulos na mesma request, ela
  precisa limpar `g._rotulos_filas_por_empresa` depois do commit.
- **A1:** o JSON do Iniciar Turno troca `unidade`/`unidade_sigla` por
  `rotulo`; a tabela "Filas do turno" mostra só o rótulo (sem o "(Unidade)"
  ao lado) e o autocomplete exibe `f.rotulo`. **A busca continua pelo nome
  puro** (`buscarEm: f.nome`): digitar "coleta" acha todas as Coletas.
- **A2:** `nome_exibicao` do JSON do Redirecionar vem de `rotulo_fila()`;
  `nome` puro continua para a busca.
- **A3 + checklists:** `{{ rotulo_fila(fila) }}` no select de Fila do Botão
  do totem e nos checklists de Local e TV ("Nome (Unidade)" -> "[SIGLA]
  Nome").
- **Tela por Local:** senha chamada, "Senhas em espera" e "Senhas atendidas"
  (HTML) e o campo `fila` dos três `_serializar_*_local` (JSON de `/dados`).

**Testes:** novo `tests/test_regressao_rotulos_filas_operacional.py` (12
verificações) - duas unidades "Anápolis - ..." sem sigla com uma "Coleta"
cada, mais a Matriz: o mesmo rótulo com " (id)" (idêntico ao dos relatórios)
no JSON do Iniciar Turno, no JSON do Redirecionar, no HTML e no `/dados` da
tela por Local, no select do Botão do totem e nos checklists de Local/TV;
busca pelo nome puro executando o JS REAL da página num Chromium (Playwright)
- "coleta" acha as Coletas no Iniciar Turno e no Redirecionar, exibidas com
rótulo, e "[ANÁ]" não acha nada (a busca não é pelo rótulo); payload
`chamada` da TV com exatamente as mesmas chaves de antes, sem rótulo nem nome
de fila, e `fila_atualizada` só com o id; `rotulo_fila()` dentro de
`app_context()` sem request funciona e reflete na hora uma sigla cadastrada
e uma fila renomeada no mesmo app context (sem cache). Ajustados (sem perder cobertura):
`test_regressao_contexto_multi_unidade.py` (lia `unidade_sigla` do JSON do
Iniciar Turno; passa a conferir `rotulo` + `nome` puro) e
`test_regressao_local_rodizio_tela.py` (`fila` do `/dados` passa a ser
"[UNI] Fila X"); `test_regressao_relatorios_fila_unidade.py` só trocou o
import. Suíte completa (70 arquivos) sem falhas.

**Arquivos alterados:** `app/utils/rotulos.py` (novo); `app/__init__.py`
(global Jinja); `app/relatorios/helpers.py` (sai `rotulos_filas`/
`_chave_colisao`, passa a importar de `app/utils/rotulos.py`);
`app/core/routes.py` (import); `app/atendimento/turno.py`;
`app/atendimento/chamada.py`; `app/templates/atendimento/iniciar_turno.html`;
`app/templates/atendimento/_redirecionar_busca.html`;
`app/templates/atendimento/painel_local.html`;
`app/templates/admin/totem_botoes/form.html`;
`app/templates/admin/locais/form.html`; `app/templates/admin/tvs/form.html`;
novo `tests/test_regressao_rotulos_filas_operacional.py`; testes ajustados
listados acima.

---

## 10.71. ADENDO — Campo "Sigla" no cadastro de Unidade + atualização opcional do prefixo dos Locais

> Pendência achada na revisão do 10.70: `Unidade.sigla` (10.22) alimenta o
> prefixo "[SIGLA]" de filas (10.69/10.70), Locais, Totens e TVs, mas a tela
> de Unidade não lia nem gravava o campo - só dava para preencher via SQL.
> Investigação (FASE 1) relatada e aprovada antes de implementar.

> **NOTA DE DEPLOY - rodar ANTES de `flask db upgrade`** (a migration
> `c7e2a9d4f1b3` cria a UniqueConstraint `(empresa_id, sigla)` e falharia com
> duplicatas, por exemplo se alguma sigla foi gravada direto no banco depois
> do 10.70):
> ```sql
> SELECT empresa_id, sigla, COUNT(*) FROM unidades
> WHERE sigla IS NOT NULL GROUP BY empresa_id, sigla HAVING COUNT(*) > 1;
> ```
> Resultado vazio = pode aplicar. Depois do deploy, cadastrar as siglas pela
> tela de Unidades (a listagem marca as automáticas e as que colidem).

**Achados da investigação:**

1. **Estado anterior:** `unidades.sigla` é `VARCHAR(4) NULL`, collation
   `utf8mb4_0900_ai_ci`, sem nenhuma constraint (só PK e o índice da FK
   `empresa_id`). `Unidade.sigla_exibicao` = `(sigla or nome[:3]).upper()` -
   o fallback pode ter acento ("ANÁ"). As rotas `nova_unidade`/
   `editar_unidade` e os templates não tinham o campo. RBAC: `before_request`
   do `admin_bp` (super admin ou perfil `admin`) + `autorizar_objeto` (404
   para outra empresa) - inalterado.
2. **Banco local:** nenhuma das 9 unidades tinha sigla; única colisão do
   fallback: "Anápolis - Centro Médico" e "Anápolis - Laborátorio" -> "ANÁ".
   Dados permitiam criar a UniqueConstraint (tudo NULL; o MySQL aceita vários
   NULL num índice único, e a collation já o torna case/accent-insensitive).
3. **Efeito colateral nos Locais:** os 14 locais locais têm
   `identificador_admin` começando com o prefixo de fallback da unidade
   (gravado pela migração do 10.32 ou pela sugestão automática do form de
   Local), às vezes com o resto editado à mão. Cadastrar uma sigla IGUAL ao
   fallback (MAT, LAR, BAN...) não muda nada; uma sigla DIFERENTE muda na hora
   o rótulo das filas, mas os locais continuam com o prefixo antigo gravado.
4. **Onde a sigla aparece:** rótulo de fila (`app/utils/rotulos.py` - todas as
   telas do 10.69/10.70), barra de contexto do admin e filtro de Unidade do
   dashboard, selects de Totem/TV no cadastro de Fila e a sugestão de
   `identificador_admin` para Local novo. **Não** afeta código da senha,
   cupom nem painel de TV (o código da senha usa `TotemBotao.sigla`, 10.47).

**Decisões (confirmadas com o cliente) e implementação:**

- **Campo "Sigla" (opcional)** em Nova/Editar unidade, com a ajuda "Usada
  para identificar filas e locais desta unidade (ex.: [MAT] Coleta). Até 4
  caracteres." (e, na edição, qual é a sigla automática atual). Validação:
  2 a 4 caracteres, só A-Z (sem acento) e dígitos, gravada em maiúsculas;
  vazio continua permitido. O formulário passou a manter o que foi digitado
  quando volta com erro.
- **Colisão com a sigla EFETIVA de outra unidade da mesma empresa** - mesma
  comparação dos rótulos (`chave_colisao`, case/accent-insensitive: "ANA"
  bate com "ANÁ"):
  - sigla digitada igual à cadastrada de outra: "A sigla X já é usada pela
    unidade Y.";
  - igual à automática de outra: "A sigla X já é usada (automaticamente)
    pela unidade Y. Cadastre primeiro uma sigla para Y.";
  - limpar a sigla quando o fallback resultante colidiria: rejeitado ("Não é
    possível remover a sigla: ...");
  - unidade NOVA sem sigla cujo fallback colidiria: rejeitada, pedindo uma
    sigla (não cria colisões novas).
  - Cadastrar a PRÓPRIA sigla automática ("MAT" na Matriz) é permitido.
    Editar outro campo de uma unidade que JÁ colide hoje (as duas "ANÁ"),
    sem mexer na sigla, continua permitido - a colisão já existia e é
    justamente o que o admin vai resolver cadastrando a sigla.
- **UniqueConstraint `uq_unidades_empresa_sigla` (empresa_id, sigla)** no
  modelo + migration `c7e2a9d4f1b3` (a partir de `b3d6f8a1c4e7`) - rede de
  segurança para corrida entre dois admins (o commit trata `IntegrityError`
  com mensagem). **Achado ao testar o downgrade no MySQL real:** ao criar o
  índice único, o MySQL descarta o índice implícito da FK `empresa_id` e
  passa a apoiar a FK no novo, que aí não pode ser removido sozinho (erro
  1553). O downgrade recria o índice simples `empresa_id` antes de remover o
  único (só no MySQL e só se não existir) - ciclo upgrade/downgrade/upgrade
  validado duas vezes no MySQL local, schema volta exatamente ao anterior.
- **Listagem de Unidades:** coluna "Sigla" com a sigla efetiva, "(automática)"
  quando é fallback, e aviso quando duas unidades têm a mesma sigla efetiva
  ("igual à de Y - cadastre uma sigla").
- **Locais - fluxo em dois passos (opção b + c da investigação):**
  - O gatilho é a sigla EFETIVA mudar ao salvar (cadastrar, trocar, limpar -
    ou renomear uma unidade sem sigla, que muda o fallback). A unidade é
    salva normalmente; se houver locais da unidade com `identificador_admin`
    começando EXATAMENTE com "[PREFIXO_ANTIGO] ", o admin vai para uma prévia
    (`/admin/unidades/<id>/prefixo-locais?prefixo_antigo=X`) com antes ->
    depois, colisões já sinalizadas, "Atualizar locais" (POST próprio, com
    CSRF) e "Agora não". **Nada nos locais muda sem esse POST.**
  - O POST **recalcula** o plano do banco (não confia na prévia) e grava
    **tudo ou nada**: checa a unicidade de todos contra a empresa inteira
    (case/accent-insensitive, mesma regra da constraint do banco); se qualquer
    um colidir, não grava nenhum e lista quais colidiram. A sigla continua
    salva. O `prefixo_antigo` vem do formulário, mas só seleciona quais
    identificadores DESTA unidade (já autorizada) começam com ele - o novo
    prefixo é sempre a sigla efetiva atual, lida do banco.
  - Só o prefixo muda; o texto depois dele nunca é tocado. O sufixo " (id)"
    sai só quando o identificador termina exatamente em " (<id do próprio
    local>)" (padrão da migração do 10.32) E o resultado sem ele continua
    único; "(sala 2)" ou um "(99)" que não é o id do local ficam.
  - **Aviso passivo** (listagem e edição de Unidades): "N locais ainda usam o
    prefixo [X]" com link para cada local e para refazer a prévia - cobre o
    "Agora não" e siglas trocadas direto no banco. Detecta prefixos de até 4
    caracteres entre colchetes (tamanho de uma sigla), pra não confundir com
    um identificador editado à mão como "[Térreo] Guichê 1".
- `_chave_colisao` (`app/utils/rotulos.py`) virou pública (`chave_colisao`) -
  passou a ser usada também em `app/admin/unidades.py`.
  `Unidade.sigla_efetiva(sigla, nome)` (staticmethod, mesma regra da
  property) valida o formulário antes de gravar.
- **Tema escuro (10.56):** os templates novos/alterados só usam classes do
  layout admin baseadas em `--gf-*` - nenhuma cor fixa. Os prints em Chromium
  (claro e escuro) mostraram que `<a>` no admin não tem regra de cor e fica
  no azul padrão do navegador, ilegível no escuro; para as telas deste
  adendo, `.link-admin a`/`a.link-admin` usam `--gf-info` (mesma cor de
  `button.link`), e as colisões usam `.badge.alerta` (sigla automática
  repetida) e `.badge.erro` (identificador na prévia), também só com tokens.
  **Achado anterior a este adendo, não corrigido aqui:** os links "Editar"/
  "Configurar rodízio" das listagens do admin têm o mesmo problema no tema
  escuro. **Nota cruzada (10.72):** corrigido na origem por `--gf-link` em
  `gestfila.css`; `.link-admin` foi removida.

**Testes:** novo `tests/test_regressao_sigla_unidade.py` (22 verificações) -
formato (1 e 5 caracteres, acento, hífen, espaço rejeitados; " pr9 " gravada
"PR9"; vazio = NULL); unicidade case-insensitive; rejeição de sigla igual à
automática de outra unidade, inclusive com acento ("ANA" x "ANÁ"); própria
sigla automática aceita; edição de unidade que já colide continua permitida;
unidade nova com fallback colidindo rejeitada; renomear unidade SEM sigla para
um nome cujo fallback colide ("Matriz Sul" -> MAT) rejeitado; UniqueConstraint no banco (e a
mesma sigla em outra empresa permitida); listagem com "(automática)" e aviso
de colisão; rótulo de fila muda na hora ("[ACM] Coleta", e a outra perde o
" (id)"); salvar leva à prévia sem alterar locais; prévia (GET) não altera
nada; aviso passivo na listagem e na edição com links; confirmação com uma
colisão não grava nenhum local e mantém a sigla; confirmação ok troca só o
prefixo, remove " (id)" só do próprio id e quando o resultado é único,
preserva "(sala 2)", "(99)" e texto editado à mão, não toca outras unidades;
POST repetido não altera nada (recalcula); limpar a sigla rejeitado quando o
fallback colidiria e aceito (com prévia de volta ao automático) quando não;
RBAC da rota nova (403 sem perfil admin, 404 para outra empresa, GET e POST).
Testes existentes de Unidade passaram sem ajuste. Suíte completa (71
arquivos) sem falhas.

**Arquivos alterados:** `app/models/unidade.py` (`__table_args__`,
`sigla_efetiva`); migration nova `c7e2a9d4f1b3_unique_sigla_unidade.py`;
`app/admin/unidades.py` (validação, prévia/confirmação, aviso passivo);
`app/templates/admin/unidades/form.html`, `listar.html`, novos
`prefixo_locais.html` e `_aviso_prefixo_locais.html`; `app/utils/rotulos.py`
(`chave_colisao` pública); novo `tests/test_regressao_sigla_unidade.py`.
Nota de deploy do 10.70 atualizada; pendência da seção 11 removida.

---

## 10.72. ADENDO — Cor dos links de conteúdo nos temas claro e escuro (correção na origem)

> Achado dos prints do 10.71: nenhum layout definia cor para `<a>`, então os
> links de conteúdo ficavam no azul (e, visitados, no roxo) padrão do
> navegador - ilegíveis no tema escuro (10.56). O 10.71 corrigiu só as telas
> dele (`.link-admin`); este adendo corrige na origem. Investigação (FASE 1)
> relatada e aprovada antes de implementar.

**Achados da investigação:**

1. **Nenhuma regra de cor para `a`/`:visited`/`:hover`/`:focus-visible`** em
   `gestfila.css` nem nos 7 layouts-raiz - só `nav a` (`--gf-nav-texto`) e
   regras pontuais com classe (`.exportar a`, `.menu-relatorios a`,
   `.top-bar a`, `.link-trocar-local`, `.botao`, classes do totem).
   `.acoes a` (admin/atendimento) só define margem/fonte, sem cor.
2. **Links que dependiam do navegador:** ações das listagens do admin
   ("Editar", "Configurar rodízio", "Unidades"/"Usuários" em empresas - 10
   telas), URL pública de Totem/TV (listagem `a.link-publico` e formulários),
   ações de `atendimento/listar_filas.html`, links de `core/index.html` e das
   páginas 403/404 (`base.html`). Relatórios (exportação, menu) e dashboards
   já tinham cor própria ou não têm links de conteúdo; breadcrumbs não têm
   `<a>`; flashes são texto escapado.
3. **Contraste medido (não presumido):** azul padrão do navegador no tema
   escuro: 1,49-1,89:1; roxo de visitado: 1,27-1,61:1. E `--gf-info`
   (#2980B9), candidato inicial da proposta, ficava ABAIXO de 4,5:1 no tema
   claro (4,3 sobre branco, 3,97 sobre o fundo da página).
4. **Painel de TV:** nenhum `<a>`, mas `painel/tv.html` TEM `<main>` - cairia
   no escopo "main" se um link fosse adicionado lá. Totem: só `<a>` com classe
   e cor própria, sem `<main>`/`.container`. Cupom: sem `<a>`; o `@media
   print` só mostra `.cupom`. PDF dos relatórios: tem `<main>`, mas não
   carrega `gestfila.css` (print.css inline, 10.67).
5. **Armadilha de especificidade:** um `a:visited` comum tem (0,1,1) - maior
   que `.botao` (0,1,0) -, então um botão "+ Nova ..." já visitado pegaria a
   cor de link.
6. **Escopo confirmado no DOM real** (Chromium, 20 páginas: listagens e
   formulários do admin, prévia do 10.71, atendimento, core/index, dashboard,
   relatórios, 403, 404, login): nenhum `<a>` fora do `<nav>` fica fora de
   `main`/`.container`. A barra de contexto do admin está fora do `<main>`,
   mas não tem `<a>`.

**Decisões (confirmadas com o cliente) e implementação:**

- **Token `--gf-link`** em `gestfila.css`: `#1D6A9C` no claro, `#5DADE2` no
  escuro (`[data-theme="dark"]`). Contraste: claro 5,16-5,84:1 sobre
  superfície, fundo da página e fundos de aviso info/alerta/sucesso (4,50:1
  sobre o fundo de flash de erro, onde hoje não há link); escuro 5,38-7,23:1
  sobre todos esses fundos.
- **Regras** (cor, hover sublinhado, `:focus-visible` com contorno
  `--gf-link`), com o seletor INTEIRO dentro de `:where()` - especificidade
  zero, então qualquer regra com classe continua vencendo, inclusive em link
  visitado:
  `:where(main a, .container a):not(:where(.area-painel-tv a))`.
  Escopo: `<main>` (admin, atendimento, relatórios, dashboard) e `.container`
  (`base.html`). Fora: `<nav>`, totem, cupom e o painel de TV - excluído
  explicitamente pela classe `area-painel-tv` no `<body>` de
  `painel/_layout.html` (achado 4). Sem regra própria de `:visited`: uma cor
  de autor em `a` vale para o estado visitado por especificação.
- **`.link-admin` (10.71) removida** - a regra global cobre o mesmo caso; uma
  fonte de verdade só. `.badge.alerta`/`.badge.erro` do 10.71 continuam.
- **Foco visível do `<nav>`:** fora deste adendo (registrado na seção 11).

**Validação no navegador real** (Chromium, temas claro e escuro, via
`getComputedStyle`): "Editar" = `--gf-link`; botão "+ Novo local" (`a.botao`)
continua branco; links do `<nav>` continuam `--gf-nav-texto`; exportação dos
relatórios continua `--gf-primaria`; foco por teclado em "Editar" mostra o
contorno sólido em `--gf-link`; um `<a>` injetado no `<main>` do painel de TV
fica com a cor do navegador, não `--gf-link` (exclusão funcionando). Prints
claro/escuro: Locais, Filas, relatório com exportação, dashboard, botão
"+ Novo local" e foco por teclado. **`:visited` não foi provado por print:**
o Chromium headless do Playwright (inclusive no modo novo) não aplicou o
estado visitado nem SEM a correção, então um print não provaria nada; a
garantia fica no teste estrutural (cor de autor em `a`, sem regra
`:visited` solta).

**Testes:** novo `tests/test_regressao_links_tema.py` (5 verificações,
estrutural, lê os valores reais do `gestfila.css`): `--gf-link` nos dois
temas com contraste >= 4,5:1 calculado sobre superfície, fundo e fundos de
aviso; seletores de link 100% dentro de `:where()`, com `main a` e
`.container a`, sem `nav` e com a exclusão do painel; nenhuma regra `a`/
`a:visited` global solta; painel marcado com `.area-painel-tv` e sem `<a>`,
totem sem `<main>`/`.container`, cupom sem `<a>`, PDF sem `gestfila.css`;
`.link-admin` ausente dos templates. Suíte completa (72 arquivos) sem falhas.

**Arquivos alterados:** `app/static/css/gestfila.css` (token + regras);
`app/templates/painel/_layout.html` (`<body class="area-painel-tv">`);
`app/templates/admin/_layout.html` (sai `.link-admin`);
`app/templates/admin/unidades/_aviso_prefixo_locais.html` e
`prefixo_locais.html` (sem `.link-admin`); novo
`tests/test_regressao_links_tema.py`.

---

## 10.73. ADENDO — Relatório "Tempos por senha" (espera e atendimento de cada senha, para exportar)

> Pedido da gestão das unidades: tempo de espera (da retirada da senha até a
> chamada) e tempo de atendimento (do início à finalização), em minutos,
> INDIVIDUAL por senha, para exportar e calcular as médias das unidades - os
> relatórios existentes são todos agregados. Investigação (FASE 1) relatada e
> aprovada antes de implementar. Implementado DEPOIS do 10.74 (dia civil da
> clínica nos agregados), a pedido do cliente, para os dois não divergirem.

**Achados da investigação:**

1. **Reaproveitamento sem pareamento novo:** `_segmentos_espera_por_senha`
   (10.66), `_pares_inicio_fim` e `_segmentos_intermediarios_realocacao`
   (10.62) devolvem os próprios logs (com `senha_id`) - basta agrupá-los por
   senha. O fallback de dado histórico vem junto, sem cópia.
2. **"A média por senha bate com o agregado" não vale por definição:** os
   agregados fazem a média POR SEGMENTO (uma senha Recepção -> Coleta conta 2
   vezes); a média POR SENHA conta cada senha uma vez. O que fecha
   exatamente: soma e contagem dos segmentos, e a média da visão por etapa.
3. **Banco local:** 423 senhas (23/07 a 25/08), no máximo 67 por dia/unidade;
   417 são dado histórico (logs sem o snapshot do 10.62) e nenhuma senha no
   formato novo foi realocada. Jornadas: 0-2 segmentos de espera e de
   atendimento por senha. Exemplo real (AMA003, histórico): espera medida 6,5
   min (a 2ª espera, 0,9 min, é invisível no fallback) e atendimento
   0,2 + 975,4 min - finalização 16h depois da chamada (valor extremo:
   justifica a mediana no resumo; nenhum corte automático).
4. **Casos sem dado:** nunca chamada (status `aguardando`); cancelada - só o
   encerramento automático cancela (`middleware/encerramento.py`, 10.18), e
   só senhas `chamada`/`em_atendimento`; chamada e não finalizada; histórico
   com realocação (o fallback só enxerga parte da jornada).

**Decisões (confirmadas com o cliente) e implementação:**

- **Uma tela "Tempos por senha"** (`/relatorios/tempos-por-senha`, item no
  menu de Relatórios), os dois tempos na mesma linha.
- **Consulta** `app/relatorios/queries.py::tempos_por_senha(filtros)` ->
  `(linhas_por_senha, linhas_por_etapa)`: chama as três funções acima e só
  agrupa por senha. Por senha: **espera inicial** (1º segmento quando abre na
  emissão - o tempo pedido), **espera total** e **atendimento total** (soma
  dos segmentos; atendimento só com 'fim_atendimento'), **etapas de
  atendimento** (nº de segmentos de atendimento - cabeçalho "Etapas de
  atendimento" na tela, CSV, Excel e PDF), fila inicial (snapshot do evento 'gerada'; sem
  snapshot e sem realocação = fila atual), fila final, operador e local da
  finalização, **Situação**.
- **Situação** (constantes `SITUACAO_*` + `DICAS_SITUACAO`, tooltip na tela):
  "Concluída", "Histórico parcial", "Aguardando (nunca chamada)", "Cancelada
  (encerramento automático)", "Chamada, não finalizada". Sem dado = VAZIO
  ("—" na tela, célula vazia na exportação), nunca 0.
- **Histórico parcial:** senha com log sem snapshot E com realocação/volta a
  aguardar - espera inicial preenchida (exata também no fallback), totais e
  etapas vazios. Histórico SEM realocação é exato e fica "Concluída".
- **Visão por etapa** (senha, nº da etapa, tipo Espera / Atendimento -
  Conclusão / Repasse, fila e local do snapshot, operador, início, fim,
  minutos, observação "Histórico parcial"): os MESMOS segmentos dos
  agregados - inclusive os do fallback histórico -, por isso fecha com eles.
  Numeração na jornada completa da senha (não pula com o filtro de operador).
- **Operador:** na tela por senha, as senhas em que o operador atendeu
  alguma etapa, com os totais da senha inteira; na visão por etapa, só as
  etapas dele (atendimento por quem chamou, igual a `tempo_atendimento`;
  espera por quem a encerrou chamando a senha). Cabeçalhos "Operador
  (finalização)" e "Local (finalização)" na visão por senha; na visão por
  etapa, coluna única "Operador (chamada/atendimento)" - numa linha de
  Espera é quem chamou a senha, numa de Atendimento é quem atendeu -,
  explicada na nota da tela. Mesmos rótulos em todos os formatos.
- **Resumo no topo:** média, mediana e quantidade POR SENHA de espera
  inicial, espera total e atendimento total ("N de M senhas" - senhas sem o
  dado ficam fora), com nota explicando que difere da média por etapa dos
  outros relatórios quando há realocação.
- **Tela:** minutos com 1 casa e ponto decimal ("5.0", como os outros
  relatórios - troca para vírgula registrada na seção 11); tabela de 12
  colunas compacta (fonte .8rem) para caber sem rolagem, com rolagem
  horizontal só como reserva em telas estreitas; emissão no fuso da clínica (filtro
  `hora_clinica`, 10.74); paginação de `LINHAS_POR_PAGINA = 100` (resumo
  sempre sobre o filtro inteiro). Filtros: unidade, fila (com filtro por
  unidade do 10.69), operador e período (janela do dia da clínica, 10.54).
- **Exportação** (mesmo `_exportar_relatorio`, log de auditoria do 10.67,
  RBAC gerencial/admin do blueprint):
  - **Excel** com abas "Por senha" e "Por etapa"; minutos como NÚMERO com
    formato `0.00`; emissão/início/fim como data-hora da clínica.
  - **CSV por senha** e **CSV por etapa** (link extra no macro de filtros,
    `exportacoes_extras`), no padrão do Excel em português: separador ";",
    decimal ",", 2 casas, data dd/mm/aaaa hh:mm, **UTF-8 com BOM** (sem o
    BOM o Excel em português quebra os acentos de "Recepção") - aberto
    direto no Excel brasileiro, os minutos chegam como número. Os CSV dos
    relatórios antigos NÃO mudaram (continuam com ","; pendência na seção 11). O CSV por etapa entra no log como
    formato "csv", `tipo_relatorio="tempos_por_senha_etapas"` (sem mexer no
    ENUM do log).
  - **PDF**: lista completa até `LIMITE_LINHAS_PDF = 500` linhas; acima
    disso, só o resumo + aviso para usar CSV/Excel. Tabela com fonte menor
    (`.tabela-larga` no `print.css`) para as 12 colunas caberem no A4.
  - `export.exportar` ganhou parâmetros opcionais (`abas`, `csv_pt_br`,
    `colunas_minutos`); `_exportar_relatorio` ganhou `abas_excel`,
    `csv_pt_br`, `colunas_minutos`, `csvs_extras`, `contexto_pdf` - os 4
    relatórios antigos não passam nenhum e continuam idênticos.
- **Sem nome do paciente** (LGPD, mesmo critério do 10.67): nenhuma coluna,
  nem na tela, nem em CSV/Excel/PDF.

**Limitação registrada (seção 11):** o filtro de Fila usa a fila ATUAL da
senha, igual aos agregados - uma senha realocada da Recepção para a Coleta
some do filtro "Recepção".

**Testes:** novo `tests/test_regressao_tempos_por_senha.py` (17
verificações): senha simples; senha com 2 realocações (valores, ordem das 6
etapas, filas inicial/final, operador da finalização); cada caso sem dado
(nunca chamada, cancelada, não finalizada, histórico parcial) e histórico
sem realocação "Concluída"; coerência com os agregados só com dado no
formato novo (soma por senha = soma do agregado; contagem e média da visão
por etapa = agregado, para espera e atendimento); tela com o relógio às 22h
de Brasília (emissão 21:50 no dia certo), tooltip, resumo "7 de 8 senhas" e
nota da média; paginação; CSV pt-BR com números; CSV por etapa; Excel com
duas abas, células numéricas (formato `0.00`) e data-hora da clínica; os
dois CSV em UTF-8 com BOM e "Recepção" intacto; cabeçalhos "Etapas de
atendimento" e "Operador (chamada/atendimento)" na tela, CSV, Excel e PDF;
filtro de operador nas duas visões; PDF real (Chromium) completo e com o
limite (só resumo + aviso); log de auditoria dos 4 formatos; RBAC (403 para
atendente) e item no menu; ausência de `nome_paciente` na tela, CSV, CSV por
etapa, Excel (as duas abas) e PDF. Suíte completa (74 arquivos) sem falhas.

**Arquivos alterados:** `app/relatorios/queries.py` (`tempos_por_senha`,
constantes de situação/etapa); `app/relatorios/routes.py` (rota,
`_exportar_relatorio` com parâmetros opcionais, `LIMITE_LINHAS_PDF`,
`LINHAS_POR_PAGINA`); `app/relatorios/export.py` (abas, CSV pt-BR, formato
numérico); novo `app/templates/relatorios/tempos_por_senha.html`;
`app/templates/relatorios/_macros.html` (`exportacoes_extras`);
`app/templates/relatorios/index.html` (menu); `app/templates/relatorios/
_layout.html` e `print.css` (estilos); novo
`tests/test_regressao_tempos_por_senha.py`.

---

## 10.74. ADENDO — Agrupamento "por dia" e "hoje" no dia civil da clínica (não de UTC) nos relatórios e no dashboard

> Achado na FASE 1 do 10.73 (relatórios por senha): os agregados e o
> dashboard decidiam "que dia é" pela data em UTC - mesma classe de bug já
> corrigida em outros call-sites pelos adendos 10.37/10.39/10.54. Feito ANTES
> do 10.73, a pedido do cliente, para o relatório novo (que mostra a emissão
> no fuso da clínica) não divergir dos agregados.

**Bugs corrigidos:**

1. `app/relatorios/queries.py::tempo_medio_espera` agrupava por
   `abertura.timestamp.date()` - dia em UTC.
2. `app/relatorios/queries.py::volume_senhas` agrupava por
   `criada_em.date()` - dia em UTC.
3. `app/core/dashboard_queries.py::_hoje_utc` (usada por `calcular_kpis` e
   `chamadas_por_hora_hoje`) definia "hoje" com `datetime.utcnow().date()`.

Efeito: a virada do dia UTC cai às 21h de Brasília - uma senha emitida às
21h50 aparecia com a data do dia SEGUINTE na coluna "Data" de Tempo de espera
e Volume (embora o FILTRO De/Até já usasse o dia da clínica desde o 10.54), e
os KPIs "hoje" e o gráfico por hora do dashboard zeravam às 21h. Confirmado
rodando o teste novo contra o código antigo: a senha das 21h50 de 08/10 saía
no dia 09/10.

**Correção:**

- `app/utils/tempo.py::para_horario_clinica(momento)` (nova) - ÚNICO ponto de
  conversão de um valor gravado em UTC naive para o fuso da clínica (aware;
  aceita também datetime já aware; `None` -> `None`). Agrupar pelo dia da
  clínica = `para_horario_clinica(coluna).date()`, nunca `coluna.date()`.
  Também será usada pelo 10.73 para a data/hora de emissão.
- `tempo_medio_espera` e `volume_senhas` passam a agrupar por
  `para_horario_clinica(...).date()`.
- Dashboard: `_hoje_utc` removida; `calcular_kpis` e `chamadas_por_hora_hoje`
  usam `janela_dia_clinica_em_utc()` (10.39, já existente) - nenhuma lógica
  de fuso nova.

**Varredura de `.date()`/`func.date`/`utcnow().date()` e formatação de
data/hora sobre colunas UTC no projeto inteiro:**

| Ponto | Situação |
|---|---|
| `relatorios/queries.py:188`, `:214`; `core/dashboard_queries.py` (`_hoje_utc`, usos em `calcular_kpis` e `chamadas_por_hora_hoje`) | **Corrigidos** (acima) |
| `relatorios/helpers.py:67` (`agora_clinica.date()`) e `:72/:79` (`strptime(...).date()` do filtro) | OK - já no fuso da clínica / datas digitadas pelo usuário |
| `isoformat()` de `criada_em`/`chamada_em`/`timestamp` em `atendimento/painel_fila.html`, `painel_local.html`, `core/dashboard.html` (incl. `TIMESTAMPS_CHAMADAS_HOJE`) | OK - o JS acrescenta "Z" e converte no navegador (`formatarHoraLocal`, `new Date(iso + "Z")`) |
| `totem/senha_gerada.html:30` (cupom) | OK - `isoformat() + "Z"`, convertido no navegador |
| `relatorios/_layout_impressao.html:43` (rodapé do PDF) | OK - `gerado_em` já vem de `agora_na_clinica()` (10.67) |
| **`relatorios/dashboard.html:68`** ("Últimas chamadas", `chamada.timestamp.strftime('%d/%m %H:%M:%S')`) | **Corrigido** (incluído neste adendo a pedido do cliente) - formatava no servidor a hora UTC, 3h a mais; agora `\| hora_clinica('%d/%m %H:%M:%S')` |
| **`admin/locais/ocupacao.html:18`** (coluna "Desde", `item.desde.strftime(...)`; `local_desde` gravado com `datetime.utcnow()` em `atendimento/local.py:108` e `turno.py:171`) | **Corrigido** (idem) - agora `\| hora_clinica('%d/%m/%Y %H:%M')` |
| `models/fila.py:80-81` (`.weekday()`/`.hour`), `middleware/encerramento.py:72` (`.time()`) | OK - sobre `agora_na_clinica()` |
| `relatorios/helpers.py:176-177` (`strftime`) | OK - datas digitadas no filtro, não colunas UTC |
| JSON de `atendimento/chamada.py:48/63/76` e `dashboard_queries.chamadas_por_hora_hoje` (`isoformat()` sem "Z") | OK - todos os consumidores JS acrescentam "Z" (`painel_fila.html:178/195`, `painel_local.html:157/174`, `core/dashboard.html:218/239`) |
| Demais `new Date()` em JS (relógio da TV, aviso das 23h do 10.18, logs de debug) | OK - "agora" no relógio do navegador, não coluna UTC |
| `tempo_espera.html:22`/`volume.html:22` (`linha.data.strftime`) | OK - `data` já é o dia da clínica depois deste adendo |

**Padrões de grep usados na varredura** (Python e templates, projeto
inteiro): `\.hour\b|\.minute\b|\.time\(\)|\.weekday\(|\.isoweekday\(|strftime\(|\.date\(\)|func\.date|datetime\.now\(|date\.today\(|utcnow\(\)\.(date|time|hour)`;
filtros Jinja `\|\s*(date|datetime|datetimeformat|strftime)`; `isoformat\(\)`
(com checagem de cada consumidor JS); `new Date\(|Date\.parse\(`.

**Filtro Jinja `hora_clinica`** (`app/utils/tempo.py::formatar_hora_clinica`,
registrado em `create_app`): formato padrão `%H:%M`, formato opcional
(`{{ coluna | hora_clinica('%d/%m/%Y %H:%M') }}`), `None` -> "".

**REGRA (registrada também na seção 8):** hora/data exibida no servidor
sempre via `para_horario_clinica()` ou o filtro `hora_clinica`; nunca
`strftime`/`.date()` direto sobre coluna UTC.
| `totem/impressao.py:53` (`momento.strftime` no cupom ESC/POS) | Código morto - `totem/routes.py:13` registra que não é mais chamado |
| Comentários/docstrings citando `func.date`/`.date()` (`atendimento/helpers.py:566`, `totem/routes.py:407-411`, `utils/tempo.py`) | Só texto explicativo, sem código |

**Testes:** novo `tests/test_regressao_dia_clinica_relatorios.py` com o relógio simulado em 09/10 01:00 UTC = 08/10 22:00 de
Brasília (patch de `app.utils.tempo._instante_utc`, mesmo padrão de
`test_regressao_fuso_horario.py`): `para_horario_clinica` (naive, aware,
None); Volume e Tempo de espera (via CSV, rota real) com a senha das 21h50 no
dia 08/10 e a das 23h30 de 07/10 no dia 07/10; KPIs "hoje" às 22h com as
chamadas do dia da clínica (2, espera média 450 s) e o gráfico por hora com a
chamada das 21h55; filtro `hora_clinica` (padrão, formato, None); "Últimas
chamadas" do dashboard de relatórios mostrando 08/10 21:55:00 (não 09/10
00:55:00) e a coluna "Desde" da ocupação de locais mostrando 08/10/2026 21:40
(não 00:40). 8 verificações; confirmado que o teste FALHA com o código antigo
(queries/dashboard e, separadamente, os dois templates). Suíte completa (73
arquivos) sem falhas.

**Arquivos alterados:** `app/utils/tempo.py` (`para_horario_clinica`,
`formatar_hora_clinica`); `app/__init__.py` (filtro `hora_clinica`);
`app/relatorios/queries.py`; `app/core/dashboard_queries.py`;
`app/templates/relatorios/dashboard.html`;
`app/templates/admin/locais/ocupacao.html`; seção 8 (regra); novo
`tests/test_regressao_dia_clinica_relatorios.py`.

---

## 10.75. ADENDO — Tempos exibidos em minutos e segundos (mm:ss / h:mm:ss), não em minuto decimal

> Relato de produção (relatório "Tempos por senha", 10.73): a tela mostrava
> "21.8" quando o esperado era "21 min 48 s" / "21:48". O valor estava certo
> (0,8 min = 48 s); o problema era o formato. Investigação (FASE 1) relatada
> e aprovada antes de implementar.

**Achados da investigação - quatro formatos de duração coexistiam:**

1. Minuto decimal na tela e no PDF dos relatórios: "Tempos por senha" (linhas
   e cartões de média/mediana, macro `minutos()`), Tempo de espera, Tempo de
   atendimento e Ranking (`(segundos / 60) | round(1)` -> "21.8 min").
2. "21m 48s" no KPI "Tempo médio de espera" do dashboard (JS `formatarTempo`).
3. "00:21:47" (sempre com hora, TRUNCADO) nas telas de atendimento: coluna de
   espera das senhas atendidas (Jinja) e o contador ao vivo "T. Espera" (JS
   `formatarHHMMSS`).
4. Exportação: "Tempos por senha" em minutos com 2 casas (Excel `0.00`, CSV
   "21,80"); Tempo de espera/atendimento/Ranking em SEGUNDOS
   (`tempo_medio_segundos`).
5. Achado extra: a média/mediana do resumo do "Tempos por senha" era
   calculada sobre o minuto JÁ arredondado a 2 casas (`*_min`), não sobre os
   segundos exatos.

**Decisões (confirmadas com o cliente) e implementação:**

- **Função única** `app/utils/tempo.py::formatar_duracao(segundos)`,
  registrada como filtro Jinja `duracao`: "mm:ss" abaixo de 1 hora (21:48,
  02:05), "h:mm:ss" a partir de 1 hora (16:15:24); segundos arredondados
  "meio para cima" (`segundos_arredondados` - o `round()` do Python
  arredondaria empates para o par) a partir dos segundos EXATOS, nunca de um
  minuto já arredondado (59:59,6 -> 1:00:00); sem dado -> "" (o template
  mostra "—"; nunca "00:00" no lugar de dado ausente, regra do 10.73);
  **duração negativa** (relógio fora de ordem num log) exibida com sinal
  ("-00:05"), sem esconder e sem mudar o cálculo.
- **Tela e PDF:** "Tempos por senha" (linhas e cartões) e Tempo de espera,
  Tempo de atendimento e Ranking com o filtro `duracao`; cabeçalhos "(mm:ss)"
  (a nota/subtítulo explica o h:mm:ss a partir de 1 hora).
- **Correção de CÁLCULO do resumo (não só apresentação):** `_resumo_tempos`
  passa a calcular média e mediana sobre os SEGUNDOS exatos, formatando só na
  exibição. Diferença < 1 s em relação ao cálculo anterior (sobre minutos
  arredondados a 2 casas). Os tempos por senha e a coerência dos 10.73/10.74
  não mudaram (testes deles sem alteração de valores).
- **Excel do "Tempos por senha"** (abas "Por senha" e "Por etapa"): DURAÇÃO
  nativa - valor = `segundos_arredondados / 86400` (fração de dia), formato
  `[h]:mm:ss` (`[h]` não volta a zero depois de 24 h) -, exibida como
  0:21:48 e somável/média direto na planilha; segundos arredondados para a
  célula mostrar exatamente o que a tela mostra. Colunas "Espera inicial /
  Espera total / Atendimento total (h:mm:ss)" e "Duração (h:mm:ss)".
- **CSV do "Tempos por senha"** (por senha e por etapa): mantém a coluna em
  minutos decimais (número com vírgula, padrão do 10.73) e ganha ao lado a
  mesma duração em texto - "Espera inicial (min)" + "Espera inicial (mm:ss)",
  ..., "Duração (min)" + "Duração (mm:ss)" (a coluna "Minutos" da visão por
  etapa virou "Duração (min)").
- **Achado e corrigido no caminho:** no CSV do 10.73, "Etapas de atendimento"
  saía "3,00" (o pandas tornava decimal uma contagem com vazios, e o
  `float_format` de 2 casas se aplicava). `export.exportar` ganhou
  `colunas_inteiras` (tipo inteiro que aceita vazio) - agora "3".
- **Nota da tela:** "No Excel, os tempos são durações (h:mm:ss): dá para
  somar ou tirar a média direto na planilha."
- **Dashboard:** `/api/dashboard/kpis` ganhou o campo
  `tempo_medio_espera_texto` (mesma função Python; `None` sem dado), mantendo
  `tempo_medio_espera_segundos` intacto (contrato preservado). O JS só exibe
  o texto - `formatarTempo` removido (não tinha outro uso). "21m 48s" ->
  "21:48".
- **Exportações dos relatórios antigos NÃO mudam** (continuam em segundos),
  pela mesma pendência do Power BI da seção 11 (observação acrescentada lá).
- **Telas de atendimento: fora deste adendo, sem mudança** (inclusive o
  truncamento): são cronômetros do operador, e a coluna estática de espera
  das senhas atendidas precisa bater, segundo a segundo, com o contador ao
  vivo "T. Espera" (JS, truncado). A contagem regressiva do encerramento das
  23h (10.18) também fica como está - é contagem até um horário, não uma
  duração medida.

**Testes:** novo `tests/test_regressao_duracao.py` (8 verificações):
formatação (21,8 min -> 21:48; 59:59,6 -> 1:00:00; 975,4 min -> 16:15:24;
02:05; meio para cima em 20,5 s e 21,5 s; 0 s -> 00:00; None -> vazio;
-5 s -> -00:05) e o filtro registrado; resumo sobre segundos exatos (10,667 s,
não 10,6 s); tela do "Tempos por senha" (linhas, cartões, cabeçalhos, nota,
negativo) e dos três agregados; PDF real (Chromium) do "Tempos por senha" e
do Tempo de espera; Excel lido no XML cru - célula NUMÉRICA (`t="n"`), valor
= segundos/86400, formato `[h]:mm:ss`, nas duas abas; CSV por senha e por
etapa com as duas colunas; valor gravado no Excel = segundos ARREDONDADOS /
86400 (1307,6 s -> 1308 s; 1307,4 s -> 1307 s; mesmo segundo da tela); dashboard com o campo numérico intacto e o texto
novo (e `None` sem dado). `tests/test_regressao_tempos_por_senha.py`
ajustado só no formato (mesmos valores, mesma coerência). Suíte completa (75
arquivos) sem falhas.

**Nota de deploy:** `git pull` + restart do `gestfila` (sem migration, sem
mudança em `requirements.txt`).

**Arquivos alterados:** `app/utils/tempo.py` (`formatar_duracao`,
`segundos_arredondados`); `app/__init__.py` (filtro `duracao`);
`app/relatorios/routes.py` (`_resumo_tempos` sobre segundos, colunas por
formato, `_duracao_excel`, `colunas_duracao`/`colunas_inteiras`);
`app/relatorios/export.py` (`FORMATO_DURACAO_EXCEL`, `colunas_duracao`,
`colunas_inteiras`); `app/core/dashboard_queries.py`
(`tempo_medio_espera_texto`); templates `relatorios/tempos_por_senha.html`,
`tempo_espera.html`, `tempo_atendimento.html`, `ranking.html`,
`core/dashboard.html`; novo `tests/test_regressao_duracao.py`;
`tests/test_regressao_tempos_por_senha.py` ajustado.

---

## 10.76. ADENDO — "Áreas": agrupamento cadastrável de filas para os relatórios

> Pedido: a clínica tem setores físicos formados por grupos de filas (filas de
> médicos do "2º Andar", Quimio/Mezanino, Laboratório) e o setor só aparecia
> no nome da fila. Agora há um agrupamento cadastrado e reutilizável nos
> relatórios. Investigação (FASE 1) relatada e aprovada antes de implementar.

**Regras de proteção (decididas antes):**
- **Nome:** "Área" na interface; `AreaFila` / `area_fila_id` no código - NÃO
  "setor", que já é conceito interno (`Senha.setor`, logs, URLs
  recepcao/coleta). A palavra "área" só existia antes em comentários e em
  classes CSS (`area-cupom`, `area-painel-tv`).
- **Escopo: só admin e relatórios.** Totem, painel de TV, tela de
  atendimento, rodízio, Iniciar Turno e Redirecionar não leem a área -
  confirmado por grep na investigação (nenhum desses módulos referencia a
  área e nada serializa a Fila inteira) e travado por teste estrutural.
- **Uma área por fila** (1:N): coluna `filas.area_fila_id` - o schema já
  impede N:N e a dupla contagem.
- **Área de UMA unidade** (decisão do cliente), com validação no servidor:
  fila só entra em área da própria unidade.
- **Histórico:** a área usada nos relatórios é a ATUAL da fila (sem snapshot
  por evento) - mudar uma fila de área muda também os períodos passados.
  Limitação documentada, ligada à pendência da "fila atual" (seção 11).
- **Filas sem área nunca somem:** "Sem área" nos agrupamentos (a soma das
  áreas é igual ao total) e a lista "Filas sem área" no admin.

**Modelo e migration** (`d4a8e2f7b915`, a partir de `c7e2a9d4f1b3`, SÓ
aditiva - nada em `senhas` nem em `logs_atendimento`):
- tabela `areas_fila` (`AreaFila`): empresa, unidade, nome (até 100), ativo;
  `uq_areas_fila_unidade_nome` (nome único por unidade);
- `filas.area_fila_id` nullable (NULL = "Sem área");
- `logs_exportacao_relatorio.area_fila_id` nullable (filtro de área na
  auditoria de exportação do 10.67 - decisão do cliente).
- FKs e índices com NOME explícito, índice criado ANTES da FK (o MySQL apoia
  a FK nele, sem índice implícito); o downgrade derruba na ordem inversa (FK
  -> índice -> coluna -> tabela). **Ciclo upgrade/downgrade validado DUAS
  vezes no MySQL local** - sem o erro 1553 do 10.71, schema volta exatamente.

**Admin** (RBAC igual ao de Filas: admin da empresa e super admin, via
`before_request` do `admin_bp` + `autorizar_objeto`; links com o token do
10.72):
- Menu "Áreas": listagem com "[SIGLA] Nome", unidade, nº de filas, status,
  Editar/Desativar/Excluir, e o quadro **"Filas sem área"** (respeita a
  unidade de contexto).
- Formulário: nome, unidade, ativa e checklist das filas da unidade
  escolhida, com busca (filtro no cliente, padrão 10.53 - fila de outra
  unidade fica escondida E desmarcada; `.checkboxes label[hidden]` já tinha
  a regra de `display`). Uma fila que hoje está em outra área aparece "(hoje
  em X)"; marcá-la mostra, ANTES de salvar, o aviso "N fila(s) serão movidas
  de outra área ao salvar: ..." (sem confirmação extra) e, depois de salvar,
  um flash por fila ("'Dra. Ana' movida de '2º Andar' para 'Laboratório'").
  Desmarcar tira a fila da área ("Sem área"). Nome único por unidade
  ignorando maiúsculas e acentos (`chave_colisao`), com a UniqueConstraint
  como rede de segurança.
- Cadastro de Fila: select "Área (opcional)" com as áreas ATIVAS da unidade
  escolhida (filtro no cliente, padrão 10.64; validado no servidor). Área
  inativa some do select - exceto quando já é a área da fila, marcada
  "(inativa)", para editar a fila não desvinculá-la sem querer. Listagem de
  Filas ganhou a coluna "Área".
- **Área desativada:** as filas continuam vinculadas e os relatórios seguem
  agrupando nela; no filtro dos relatórios aparece com "(inativa)".

**Exclusão segura:**
- Excluir Área: bloqueada com filas vinculadas (ativas ou inativas) ou com
  exportação registrada com o filtro dela (auditoria) - desativar é sempre
  possível. **Decisão aprovada pelo cliente:** o bloqueio por uso em
  exportação é deliberado - o log de auditoria do 10.67 não se apaga, e a FK
  `fk_logs_exportacao_area_fila` impediria a exclusão de qualquer forma.
- Excluir Empresa: `AreaFila` entrou na lista de bloqueios (sem isso daria
  erro de FK, 500).
- Excluir Fila: nada muda pela área (o vínculo é coluna da própria fila).
  **Achado e corrigido no caminho:** `logs_exportacao_relatorio.fila_id` é
  FK, e uma fila SEM senhas mas já usada como filtro numa exportação dava
  erro de integridade (500) ao ser excluída (desde o 10.67) - agora é
  bloqueada com mensagem, como o histórico de senhas.
- Unidade: não tem exclusão (só ativar/desativar).

**Relatórios:**
- `Filtros` ganhou `area_fila_id` e `agrupar_por_area`, com default (os 7
  pontos que montam `Filtros` à mão - dashboard e testes - não mudaram).
  `resolver_filtros` valida a área contra empresa e unidades visíveis,
  descarta área de outra unidade e fila fora da área (mesmo padrão
  silencioso do 10.69).
- **Filtro "Área"** (logo após Unidade, em todos os relatórios): a Unidade
  filtra as áreas e a Área filtra as filas no select (JS; o mapa fila ->
  área vai num JSON - `opcoes.area_da_fila` -, mantendo o markup do 10.69
  intacto). `_senhas_filtradas`: senhas cuja fila ATUAL está na área.
- **"Agrupar por: Fila | Área"** em Tempo de espera, Volume e Tempo de
  atendimento (no Ranking, que é por operador, só o filtro): MESMOS
  segmentos e MESMO cálculo, só a chave de agrupamento muda (a área ATUAL da
  fila de cada segmento); "Sem área" sempre presente quando houver; no Tempo
  de atendimento agrupado, a coluna Local sai (um local atende várias filas)
  e segmento histórico sem fila (10.62) cai em "Sem área".
- **Semântica (decisão do cliente, documentada também na nota da tela):** o
  FILTRO seleciona senhas pela fila ATUAL (final) da senha, com a jornada
  inteira; o AGRUPAMENTO usa a fila de CADA segmento. Ex.: uma senha
  Recepção -> Coleta, filtrando a área da Coleta, entra com a espera na
  Recepção também - e, agrupando por área, essa espera aparece sob a área da
  Recepção. É o mesmo comportamento do filtro de Fila; filtrar pelas filas
  por onde a senha passou continua na pendência da seção 11.
- **Tempos por senha:** coluna "Área" (área atual da fila final) na tela,
  CSV, Excel e PDF (também na aba "Por etapa", pela fila da etapa); com o
  filtro de Área, quadro **"Por fila"** abaixo do resumo (senhas, média e
  mediana de espera total e atendimento total por fila final, sobre os
  segundos exatos) e a nota da semântica do filtro.
- **Rótulo:** "[SIGLA] Nome" (`rotulos_areas` em `app/utils/rotulos.py`,
  mesmo desempate " (id)" do 10.69 - agora `_com_desempate`, compartilhado
  com `rotulos_filas`); nas exportações, o nome puro (a unidade vem em
  coluna própria).
- **Exportações:** com "Agrupar por: Área", a coluna "area" ocupa o lugar de
  "fila" no CSV/Excel dos agregados; sem agrupamento o formato antigo NÃO
  muda. **Decisão aprovada pelo cliente:** a coluna "area" existe SÓ no
  CSV/Excel agrupado por área, por causa da pendência do Power BI (seção 11)
  - nenhuma coluna nova nas exportações padrão dos relatórios antigos. PDF: "Área: ..." e "Agrupado por:
  Área" no cabeçalho (`filtros_legiveis`). Log de auditoria grava
  `area_fila_id`.

**Testes:** novo `tests/test_regressao_areas_fila.py` (19 verificações): RBAC
(gerencial 403) e isolamento multi-tenant (listagem e 404 na edição da área
de outra empresa; `area_fila_id` de outra empresa ignorado no relatório e no
log); validação (fila de outra unidade, nome repetido); fila em uma área só;
resumo de fila movida no flash e aviso ANTES de salvar com o JS real
(Chromium); select de Área no cadastro de Fila; "Filas sem área"; filtro por
área = união das filas; Tempo de espera agrupado por área ("Sem área"
presente, soma = total em quantidade e em tempo) e o mesmo para Volume e
Tempo de atendimento; área inativa com "(inativa)" no filtro e seguindo no
agrupamento; área inativa só aparece no cadastro da fila que já está nela;
mudar a fila de área muda o passado (limitação); PDF real com "Área:" e
"Agrupado por: Área"; log com `area_fila_id`; quadro "Por fila" e nota no
Tempos por senha; exclusão de área (bloqueios e caso permitido), de empresa
com áreas e de fila usada em exportação; nenhum `area_fila` em totem, painel
e atendimento. `tests/test_regressao_tempos_por_senha.py` ajustado: as linhas
da tabela passaram a ser identificadas pela coluna "Senha" com os códigos
EXATOS e na ordem (`senhas_na_tabela`), em vez de contar células que começam
com "S" - a coluna "Área" mostra "Sem área", que também começa com "S".
Confirmado por mutação (senha S2 apagada antes da verificação): a asserção
nova falha mostrando `['S1', 'S3', 'S4']` no lugar de `['S1', 'S2', 'S3']`. Suíte completa (76 arquivos) sem falhas.

**Nota de deploy:** `git pull`, `flask db upgrade` (migration
`d4a8e2f7b915`, só aditiva) e restart do `gestfila`; sem mudança em
`requirements.txt`. Depois, cadastrar as áreas pela tela "Áreas".

**Arquivos alterados:** novo `app/models/area_fila.py`;
`app/models/fila.py` (`area_fila_id`, índice), `app/models/
log_exportacao.py` (`area_fila_id`, índice), `app/models/__init__.py`; nova
migration `d4a8e2f7b915_areas_fila.py`; novo `app/admin/areas.py`;
`app/admin/__init__.py`; `app/admin/filas.py` (select de Área, validação,
exclusão com exportação); `app/admin/empresas.py` (bloqueio por áreas);
`app/utils/rotulos.py` (`rotulos_areas`, `_com_desempate`);
`app/relatorios/helpers.py` (`Filtros`, `resolver_filtros`, opções,
querystring, `filtros_legiveis`, log, `MapasNomes`, `rotulo_area`);
`app/relatorios/queries.py` (filtro e agrupamento por área, área no Tempos
por senha); `app/relatorios/routes.py` (colunas, quadro "Por fila");
templates `admin/areas/listar.html` e `form.html` (novos),
`admin/_layout.html` (menu), `admin/filas/form.html` e `listar.html`,
`relatorios/_macros.html`, `tempo_espera.html`, `volume.html`,
`tempo_atendimento.html`, `tempos_por_senha.html`, `_layout.html` e
`print.css`; novo `tests/test_regressao_areas_fila.py`;
`tests/test_regressao_tempos_por_senha.py` ajustado.

---

## 10.77. ADENDO — Senhas abertas de dias anteriores: encerramento por dia da clínica + filtro de "hoje" em toda fila de espera

> Problema real em produção: ~450 senhas com status `aguardando` de DIAS
> ANTERIORES, nunca finalizadas (confirmado no banco). Investigação (FASE 1)
> relatada e aprovada antes de implementar.

**Causas confirmadas na investigação:**

1. **O encerramento nunca tocava `aguardando`:** `cancelar_senhas_em_andamento`
   (10.18) filtrava só `chamada`/`em_atendimento` - por decisão do 10.18 (o
   teste dele inclusive verificava que `aguardando` ficava intacta). Senha
   emitida e nunca chamada ficava aberta para sempre.
2. **Gatilho só por relógio:** o `before_request` só agia se
   `agora_na_clinica().time() >= 23:00`. À meia-noite a condição volta a ser
   falsa - se nenhum request chegasse entre 23:00 e 23:59, nada era encerrado
   (às 07:00 do dia seguinte, `07:00 < 23:00`).
3. **Contagens e listas de `aguardando` sem janela de dia** - 9 pontos (tabela
   abaixo), incluindo o KPI "Senhas aguardando agora" e "Senhas em espera".
4. **Pior que o suspeito na fila:** o Chamar automático (FIFO, rodízio de
   prioridade e rodízio por Local) ordena por `criada_em ASC` - a senha
   esquecida de um dia anterior era SEMPRE a primeira chamada; e o "Chamar
   por código" usava `.first()` sem ordem - com o sequencial zerando por dia
   (10.39), o BSG003 de ontem e o de hoje coexistiam e o banco devolvia
   qualquer um.

**Correção:**

- **Encerramento por DIA da clínica** (`app/middleware/encerramento.py`,
  `encerrar_senhas_de_dias_anteriores`): toda senha aberta (`STATUS_ABERTOS`
  = aguardando/chamada/em_atendimento, `app/models/senha.py`) com
  `criada_em` antes da meia-noite de hoje da clínica
  (`janela_dia_clinica_em_utc()`, 10.39 - nunca `.date()` em UTC) vira
  `cancelada` (nunca DELETE, seção 8), com `LogAtendimento` de
  `usuario_id=NULL` e snapshot `fila_id`/`local_id` (10.62):
  - `aguardando` -> evento NOVO **`expirada_aguardando`** (migration
    `e5b7c2d9a4f8`, só o ENUM `evento_log`, padrão da `c3d9f4a1e6b2`). Nome
    escolhido em vez de "expirada_sem_chamada" porque uma senha `aguardando`
    pode já ter sido chamada antes (realocação / "voltar a aguardar").
  - `chamada`/`em_atendimento` -> `encerramento_automatico_sistema` (mesma
    semântica do 10.18).
  - Roda no 1º request de cada dia da clínica, A QUALQUER HORA (antes do
    teste de relógio das 23h, que continua igual para as senhas de hoje).
    Memo "dia já varrido" por processo em `app.extensions` - evita uma query
    por request (TV, polling, estáticos); é seguro porque, depois da
    varredura, nenhuma senha de antes de hoje volta a ficar aberta. Memo
    gravado só depois do commit. Uma transação só. A query das senhas
    pendentes (`senhas_abertas_de_dias_anteriores`) fica em
    `app/models/senha.py`, ao lado de `criada_hoje_na_clinica` - base única
    da varredura, do comando e do alerta (Relatórios não importa do
    middleware).
  - **Falha da varredura não derruba o sistema:** exceção -> rollback + log
    (`logger.exception`) e o request segue normalmente (sem isso, um erro num
    `before_request` global seria 500 em TODA tela, totem e TV inclusive); o
    memo não é gravado. **Intervalo mínimo entre tentativas:**
    `INTERVALO_RETENTATIVA_VARREDURA` = 5 min (instante da última falha em
    `app.extensions`) - TV e totem fazem polling, e sem o intervalo uma falha
    persistente repetiria a query e o stack trace no log a cada request. Na
    primeira tentativa que der certo, a marca de falha é apagada e o memo do
    dia é gravado.
- **UPDATE condicional contra corrida (destaque):** o cancelamento passou a
  ser `UPDATE senhas SET status='cancelada' WHERE id=:id AND status=:lido`, e
  o log só é gravado se `rowcount == 1` (`_cancelar_se_status_ainda_for`).
  Aplicado à varredura nova **e também ao `cancelar_senhas_em_andamento` das
  23h (10.18)**, que tinha o mesmo risco latente: dois requests concorrentes
  (greenlets do eventlet) ou o comando abaixo rodando junto com o servidor
  liam a mesma senha aberta e gravavam o log de auditoria em dobro.
- **Comando `flask encerrar-pendentes --antes-de-hoje [--dry-run]`**
  (`app/comandos.py`, registrado em `create_app` - primeiro comando CLI do
  projeto): mostra a contagem por dia da clínica / empresa / unidade / fila /
  status e chama a MESMA `encerrar_senhas_de_dias_anteriores`, numa
  transação (um commit no fim; em erro, rollback e "nada foi gravado").
  `--dry-run` só faz SELECT e termina com `db.session.rollback()` explícito.
  Sem `--antes-de-hoje` o comando recusa (nunca encerra senhas de hoje).
  Idempotente. **Não depende de `ENCERRAMENTO_SESSOES_ATIVO`** - o gate só
  liga/desliga o gatilho automático do middleware; o comando age sempre
  (testado com o gate desligado).
- **Migration `e5b7c2d9a4f8`:** o ALTER de ENUM do MySQL substitui a lista
  inteira - a lista antiga tem os 8 valores existentes (os de
  `EVENTO_LOG` antes do 10.77, mesma ordem da `c3d9f4a1e6b2`) e o novo vai
  no fim. **Downgrade recusa** (`RuntimeError` com a contagem) se existir
  alguma linha `expirada_aguardando`: fora do modo estrito, o MySQL gravaria
  '' no lugar do valor removido e apagaria o evento da trilha de auditoria em
  silêncio. Verificado num SQLite temporário (stamp + linha inserida): o
  `flask db downgrade` para com a mensagem e a versão continua
  `e5b7c2d9a4f8`. O ALTER em si só roda em MySQL (`mysql.ENUM`, mesma
  limitação da `c3d9f4a1e6b2`).
- **Filtro do dia da clínica em todo ponto que lista/conta/seleciona
  `aguardando`** (defesa em profundidade): helper único
  `criada_hoje_na_clinica()` (`app/models/senha.py`; só o limite inferior da
  janela do 10.39 - não existe `criada_em` no futuro):

| # | Ponto | Uso |
|---|---|---|
| 1 | `core/dashboard_queries.py::calcular_kpis` | KPI "Senhas aguardando agora" (e `/api/dashboard/kpis`) |
| 2 | `core/dashboard_queries.py::filas_com_status` | contagem por fila do dashboard |
| 3 | `relatorios/queries.py::senhas_aguardando_por_fila` | dashboard de Relatórios |
| 4 | `atendimento/filas.py::listar_filas` | contagens "aguardando"/"prioridade" |
| 5 | `atendimento/helpers.py::senhas_aguardando` | "Senhas em espera" (tela por fila, HTML e JSON) |
| 6 | `atendimento/helpers.py::senhas_aguardando_local` | "Senhas em espera" (tela por Local, HTML e JSON) |
| 7 | `atendimento/helpers.py::selecionar_proxima_senha` | Chamar automático: FIFO, rodízio de prioridade e (via `selecionar_proxima_fila_e_senha`) rodízio por Local |
| 8 | `atendimento/chamada.py::chamar_local` | Chamar por código (tela por Local) |
| 9 | `atendimento/chamada.py::chamar` | Chamar por código (tela por fila) |

  Não precisam: a criação no totem (`_emitir_senha`) e realocar/voltar a
  aguardar (`criada_em` original preservado).

  **Sem filtro de dia, de propósito: `senha_atual_no_local`**
  (`atendimento/helpers.py` - a "Senha chamada" do posto, que também decide
  o bloqueio "Finalize o atendimento atual antes de chamar o próximo"). Ela
  procura `chamada`/`em_atendimento` do operador no Local, sem janela de dia.
  Se a varredura falhar, uma senha `chamada` de ontem no nome do operador o
  deixa bloqueado até a limpeza (varredura na próxima tentativa ou `flask
  encerrar-pendentes`). Filtrar por dia aqui esconderia a senha da tela e
  liberaria o Chamar com uma senha ainda "em andamento" no banco - por isso
  não foi feito. A falha não fica escondida: o alerta do dashboard de
  Relatórios a mostra. Achado ao escrever o teste.
- **Alerta no dashboard de Relatórios** ("N senha(s) aberta(s) de dias
  anteriores", `coletar_alertas` + `contar_senhas_abertas_dias_anteriores`,
  escopado às unidades visíveis): com a varredura funcionando é sempre 0 - o
  alerta existe para que uma falha dela NÃO fique mascarada pelo filtro de
  dia das telas. O título da caixa passou de "Alertas de configuração" para
  "Alertas".
- **Tempos por senha (10.73):** situação decidida pelo HISTÓRICO -
  `expirada_aguardando` sem chamada anterior -> **"Expirada (nunca
  chamada)"**; com chamada anterior -> **"Expirada (após realocação/volta à
  espera)"** (com dica no tooltip). Antes, uma `cancelada` nunca chamada
  cairia em "Aguardando (nunca chamada)". O evento NÃO entra em
  `EVENTOS_ABERTURA_ESPERA`: a espera que terminou em expiração continua
  descartada (segmento aberto) e nunca entra em tempos/médias.

**Testes:** novo `tests/test_regressao_senhas_dias_anteriores.py` (27
verificações; relógio simulado em 15/10 07:00 de Brasília via
`_instante_utc`; nenhum request às 23h): com a varredura desligada, os 9
pontos só listam/contam/chamam as senhas de hoje (inclusive FA003 de ontem x
FA003 de hoje no Chamar por código, nas duas telas) e o alerta mostra "5
senha(s)"; o 1º request às 07:00 encerra as 5 abertas de ontem com os eventos
e snapshots certos (inclusive a das 21:50 de ontem, que já é 15/10 em UTC) e
deixa as de hoje e a finalizada intactas; idempotência (request e chamada
direta); o alerta some depois da varredura; UPDATE condicional (corrida
perdida não grava log; 23h rodado 2x grava 1 log); virada para o dia seguinte
com o memo; falha da varredura responde 200 e não grava o memo; dentro de 5
min da falha, requests seguidos não tentam de novo; depois de 5 min tenta,
encerra, grava o memo e limpa a falha; comando com o gate do middleware
desligado (recusa sem `--antes-de-hoje`, `--dry-run` sem alterar, execução
real, reexecução com 0); as duas situações novas no Tempos por senha (consulta e
tela) e a espera expirada fora dos tempos.
`tests/test_regressao_horario_relogio.py` ajustado: cria senhas com data fixa
(23/07/2026) e espera vê-las em "Senhas em espera" - passou a fixar o relógio
naquele dia. Suíte completa (77 arquivos) sem falhas.

**Pendência registrada (seção 11), não corrigida aqui:** `TurnoAtivo`/
`Usuario.local_atual_id` de dias anteriores também ficam abertos pela mesma
causa.

**Roteiro de deploy (nesta ordem - limpeza ANTES do restart):**

1. `git pull`
2. `flask db upgrade` (migration `e5b7c2d9a4f8`, só ENUM; necessária antes
   da limpeza real, que grava `expirada_aguardando`)
3. `flask encerrar-pendentes --antes-de-hoje --dry-run` - conferir a
   contagem contra a query de investigação (GROUP BY empresa/unidade/fila/
   dia)
4. `flask encerrar-pendentes --antes-de-hoje` - limpeza real, uma transação
5. restart do `gestfila`

Por que a limpeza antes do restart: o restart sobe o código novo, cujo 1º
request já faria a varredura sozinho (sem relatório e num momento qualquer).
Rodando o comando antes, a limpeza acontece de forma controlada, com o
resumo na tela, e o 1º request depois do restart não acha nada (no-op).
Entre o `git pull` e o restart o servidor antigo continua no ar: as telas
ainda não filtram por dia, mas depois do passo 4 não sobra senha de dias
anteriores para aparecer. Sem mudança em `requirements.txt`.

**Arquivos alterados:** `app/middleware/encerramento.py`;
`app/models/senha.py` (`STATUS_ABERTOS`, `criada_hoje_na_clinica`,
`senhas_abertas_de_dias_anteriores`);
`app/models/log_atendimento.py`; nova migration
`e5b7c2d9a4f8_evento_expirada_aguardando.py`; novo `app/comandos.py`;
`app/__init__.py`; `app/core/dashboard_queries.py`;
`app/relatorios/queries.py`; `app/relatorios/routes.py`;
`app/atendimento/filas.py`; `app/atendimento/helpers.py`;
`app/atendimento/chamada.py`; `app/templates/relatorios/dashboard.html`;
seções 8 e 10.18 (notas cruzadas); novo
`tests/test_regressao_senhas_dias_anteriores.py`;
`tests/test_regressao_horario_relogio.py`.

---

## 11. Pontos abertos para confirmar com o cliente/clínica antes da Fase 4+

- **`TurnoAtivo`/`Usuario.local_atual_id` de dias anteriores ficam abertos**
  (achado na FASE 1 do 10.77; só registrado, não corrigido, a pedido do
  cliente): mesma causa do gatilho só por relógio do 10.18, e mais ampla - o
  backup server-side só encerra o turno do `current_user` que fizer um
  request no blueprint `atendimento` depois das 23h; os demais operadores só
  saem pelo logout ou pelo JS da aba. Um `local_atual_id` que sobra de ontem
  bloqueia o Local no dia seguinte ("outro operador já ocupa",
  `atendimento/local.py` e `turno.py`) até alguém usar "Desconectar" na tela
  de ocupação. Correção natural: estender a varredura por dia do 10.77
  (encerrar `TurnoAtivo` iniciado antes de hoje e liberar `local_atual_id`
  com `local_desde` antes de hoje). Conferir em produção:
  `SELECT id, local_atual_id, local_desde FROM usuarios WHERE local_atual_id
  IS NOT NULL;` e `SELECT usuario_id, local_id, iniciado_em FROM
  turnos_ativos WHERE ativo = 1;`.
- **Padronizar o formato numérico dos relatórios antigos** (registrado no
  10.73): (1) CSV de Tempo de espera, Tempo de atendimento, Volume e Ranking
  para o padrão do Excel em português (";" e "," decimal, UTF-8 com BOM, como
  o "Tempos por senha") - ANTES, verificar se algum consumidor automatizado
  (ex.: Power BI) importa os CSV atuais com "," e ".", porque a troca
  quebraria essa importação; (2) decimal com vírgula na TELA de todos os
  relatórios (hoje "5.0", com ponto, por consistência). **Observação (10.75):**
  na tela/PDF os tempos já viraram "mm:ss" em todos os relatórios, mas as
  EXPORTAÇÕES de Tempo de espera, Tempo de atendimento e Ranking continuam
  com `tempo_medio_segundos` em segundos - qualquer mudança nelas (duração
  nativa no Excel, como no "Tempos por senha") entra nesta mesma pendência,
  depois da verificação do Power BI.
- **Filtro de Fila dos relatórios usa a fila ATUAL da senha** (limitação
  registrada no 10.73, alinhada aos agregados na v1): uma senha realocada da
  Recepção para a Coleta some do filtro "Recepção" em todos os relatórios
  (inclusive "Tempos por senha"). Alternativa futura: filtrar pelas filas por
  onde a senha passou (snapshots de `logs_atendimento`, 10.62). **Vale também
  para o filtro de Área (10.76):** a área usada é a ATUAL da fila (sem
  snapshot por evento) - mudar uma fila de área muda os períodos passados -,
  e o filtro seleciona as senhas pela fila atual, enquanto o agrupamento usa
  a fila de cada segmento.
- **Ajuste visual único no admin** (registrado no 10.76): (1) os `<select>`
  dos formulários do admin (ex.: "Unidade" nos cadastros de Fila, Área etc.)
  não têm estilo de autor - aparecem pequenos, no padrão do navegador, ao
  lado dos `<input>` estilizados; (2) nas listagens, as ações
  "Desativar"/"Excluir" (formulários inline) ficam coladas, sem espaçamento.
  Corrigir uma vez, em `admin/_layout.html`, para todas as telas.
- **Seleção múltipla avulsa de filas no filtro dos relatórios** (opção
  complementar às Áreas do 10.76): escolher várias filas sem precisar
  cadastrar uma área - útil para recortes eventuais.
- **Instrumentação TEMPORÁRIA `?debug_voz=1` no painel de TV** (commits
  `89db967` e `f0c7e47`, 25/08/2026 - registrada aqui na preparação do deploy
  da leva 10.69-10.74, sem mudança de código): overlay na própria tela da TV
  com `speechSynthesis.getVoices()`, eventos do TTS e os "testes isolados A/B"
  (voz explícita x `AudioContext` prévio), para investigar "TV sem áudio no
  TTS" numa Samsung Internet/Tizen específica. Não é o `?debug_voz=1` do
  adendo 10.33 (aquele já foi removido). Fica em `app/templates/painel/
  tv.html` (constante `DEBUG_VOZ`, `criarOverlayDebugVoz`,
  `logarOverlayDebugVoz`, `logarVozesDebugVoz`, os pontos de log no fluxo de
  fala e o bloco `if (DEBUG_VOZ)` dos testes A/B). **Inerte em produção:** só
  liga com `?debug_voz=1` na URL da TV; sem o parâmetro, nada é desenhado nem
  executado. **Critério de remoção:** remover tudo assim que o diagnóstico
  for fechado - causa identificada e corrigida, ou decisão registrada de não
  corrigir -, depois de rodar os testes A/B na TV afetada e registrar o
  resultado no adendo da correção; na remoção, revalidar o render do painel
  sem erro de JS e rodar a suíte (mesmo procedimento do 10.33). Não deve
  sobreviver indefinidamente em produção.
- **Foco visível por teclado no `<nav>`** (acessibilidade, fora do 10.72):
  os links do menu principal não têm `:focus-visible` próprio - o contorno
  `--gf-link` do 10.72 é só para a área de conteúdo e teria pouco contraste
  sobre o fundo escuro fixo do `<nav>` (`--gf-escuro-fixo`). Definir um foco
  próprio para o `<nav>` (ex.: contorno em `--gf-branco`).
- **Select "Operador" dos relatórios (tempo_atendimento) não filtra pela
  Unidade escolhida** (achado na FASE 1 do 10.69, fora do escopo dele): lista
  todos os usuários visíveis da empresa - inclusive perfis admin/gerencial
  (que não atendem) e usuários sem nenhuma unidade vinculada. Corrigir exige
  `data-unidade-id` com uma LISTA de unidades (`Usuario.unidades` é M:N), não
  um id só como no select de Fila.
- ~~**Fuso horário do servidor de produção**~~ **RESOLVIDO (10.37):** o fuso da
  clínica agora é resolvido no código (`app/utils/tempo.py`, config
  `FUSO_HORARIO`, default Brasília), não depende mais do fuso do SO. Só ajuste
  `FUSO_HORARIO` no `.env` se a clínica não estiver no horário de Brasília.
- Impressão física da senha: impressora térmica local no totem (ESC/POS) ou só exibição em tela?
- Padding numérico definitivo (3 dígitos como `BR009` é fixo ou configurável por serviço?).
- Texto exato do áudio de chamada (modelo de frase).
- Lista real de unidades/serviços/siglas da clínica (para popular o seed inicial).
- **Perfil `medico` separado de `recepcao`/`coleta`** (avaliado em sessão de
  trabalho, não implementado): hoje médicos usam `coleta`/`recepcao_coleta`
  pra acessar a tela de atendimento, já que `exigir_acesso_setor`
  (`app/atendimento/helpers.py`) só reconhece os setores `recepcao`/`coleta`
  na URL (`<any(recepcao,coleta):setor>`). Um perfil "medico" isolado, por si
  só, não daria acesso a nada — exigiria estender `PERFIS_POR_SETOR` e,
  possivelmente, um terceiro `setor` de verdade (migration no `ENUM` de
  `senhas.setor`/`logs_atendimento.setor`, mudança nos templates e nos
  relatórios que agrupam por setor). Não implementar sem demanda real
  identificada (ex.: precisar restringir médico a só a própria fila/
  consultório, diferente de um operador de coleta genérico).
- **`flask db upgrade` do zero falha em SQLite na migration `2f153fe29e46`**
  (achado durante a verificação da 10.47, não corrigido - fora do escopo
  daquela tarefa): `batch_alter_table('filas', ...)` nessa migration cria uma
  foreign key sem nome explícito, e o modo batch do SQLite (usado só em
  ambiente de desenvolvimento/teste, sem servidor MySQL disponível) exige
  `ValueError: Constraint must have a name` ao tentar recriar a tabela do
  zero. Não afeta produção (MySQL não usa batch mode) nem a suíte de testes
  (que usa `db.create_all()`, não `flask db upgrade`) - só apareceria numa
  tentativa de rodar a cadeia de migrations completa do zero num banco
  SQLite local. Corrigir nomeando a constraint na migration original (ou
  documentar `db.create_all()` como o caminho suportado para SQLite) se for
  necessário validar upgrades encadeados fora de MySQL no futuro.
