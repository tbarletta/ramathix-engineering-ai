# Ramathix Engineering AI

O **Ramathix Engineering AI (REA)** é uma plataforma local, governada e multiagente para
engenharia de software. Ela funciona como uma organização de engenharia no terminal: compreende
repositórios, transforma objetivos estratégicos em trabalho executável, implementa mudanças,
valida código, revisa resultados e abre Pull Requests.

A partir da versão 1.1, o REA também possui um ciclo de **autoevolução autônoma e mensurável**.
Ele pode observar falhas recorrentes, criar hipóteses de melhoria, produzir candidatos com o
motor Level 6, comparar o candidato com a versão de referência, aprender com o resultado e
promover apenas mudanças que respeitem os limites determinísticos de segurança.

O princípio central do projeto é:

> O modelo raciocina e propõe. Componentes determinísticos decidem o que pode ser executado.

Nenhum LLM recebe autoridade irrestrita para alterar repositórios, gastar recursos, modificar
governança ou escrever em produção.

## Características principais

- Execução 100% local com modelos servidos pelo Ollama.
- Separação entre raciocínio do LLM e autoridade de execução.
- Conversação em português brasileiro.
- Mapeamento determinístico de código, arquitetura, dependências, AST e histórico Git.
- Organização de trabalho em RFCs, iniciativas, projetos e unidades de trabalho.
- Equipe multiagente com liderança técnica, desenvolvimento, revisão e especialistas.
- Execução Level 6: Issue → worktree → implementação → testes → revisão → PR.
- Sandbox Docker sem acesso à rede por padrão.
- Governança de segurança, performance, nuvem, custos e produção.
- Políticas determinísticas para comandos locais.
- Registro de auditoria append-only com remoção de segredos.
- Diagnóstico de produção somente leitura.
- Autoevolução baseada em evidência, benchmarks e comparação com baseline.
- Memória episódica de sucessos, falhas e correções.
- Orçamento de GPU, circuit breaker, locks e retomada após reinicialização.
- Otimização adaptativa da seleção de modelos e prompts.
- Promoção automática limitada a mudanças comprovadamente seguras.
- Shadow deployment, canário limitado e rollback verificável.
- Criação, validação, ativação, observação e rollback autônomo de skills.

## Requisitos

- Python 3.11 ou superior;
- Git;
- Docker;
- Ollama em execução;
- um modelo local compatível;
- token GitHub para repositórios privados, Issues e Pull Requests;
- runner self-hosted do GitHub Actions para a CI atual.

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

No Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
Copy-Item .env.example .env
```

Inicie o Ollama antes de executar o REA:

```bash
ollama serve
```

## Início rápido

Para abrir a sessão conversacional:

```bash
rea
```

A sessão:

1. detecta se o diretório atual é um repositório Git;
2. atualiza o inventário determinístico do Knowledge Engine;
3. fornece ao modelo um resumo factual e limitado do repositório;
4. mantém o contexto das últimas mensagens da sessão;
5. encaminha ações mutáveis para o controlador de governança.

Digite `/ajuda` para consultar os comandos conversacionais e `/exit` para encerrar.

## Como uma solicitação é processada

Cada mensagem passa pelo seguinte fluxo:

1. padrões determinísticos verificam comandos como `/aprovar`, `/cancelar`, `/status`,
   mudança de modo, URLs GitHub e solicitações conhecidas;
2. se nenhum padrão for encontrado, um classificador local identifica a intenção;
3. leituras seguras podem ser executadas imediatamente;
4. ações mutáveis exibem uma prévia e aguardam aprovação;
5. a política de comandos e o motor de risco avaliam a ação;
6. a execução real é realizada por componentes Python governados;
7. o resultado é registrado no audit log antes de ser apresentado como concluído.

O REA nunca descreve uma operação como concluída antes de receber o resultado real do executor.

## Exemplos de solicitações

Na conversa, você pode pedir:

- “Analise este repositório.”
- “Clone https://github.com/owner/repo.”
- “Mostre o package.json.”
- “Qual é o status do Git?”
- “Crie uma RFC para melhorar a confiabilidade do sistema.”
- “Monte um roadmap para reduzir o custo de infraestrutura.”
- “Implemente a fase 1.”
- “Execute a Issue 42.”
- “Analise este incidente de produção.”

## Modos da sessão

### Modo padrão

```text
/modo padrao
```

Leituras seguras são executadas diretamente. Mudanças locais, GitHub e operações compartilhadas
pedem aprovação.

### Modo de planejamento

```text
/modo planejamento
```

Permite análise, RFCs e roadmaps, mas impede ações que alterem o repositório ou o GitHub.

### Modo automático

```text
/modo automatico
```

Pode executar automaticamente mudanças Git locais e reversíveis. Publicação de Issues,
execução Level 6, custos, push, merge e produção continuam sujeitos às regras específicas.

## Arquitetura

```text
src/rea/
├── cli.py                    sessão conversacional e comandos principais
├── conversation.py           conversa, streaming e contexto
├── conversation_actions.py   roteamento de intenção e ações pendentes
├── config.py                 resolução de configurações
├── models.py                 Ollama e roteamento de modelos
├── policy.py                 política determinística de comandos
├── execution.py              execução local governada
├── sandbox.py                execução isolada em Docker
├── audit.py                  auditoria persistente
├── github.py                 cliente GitHub
├── knowledge/                engenharia reversa determinística
├── organization/             objetivos, RFCs, projetos e portfólio
├── team/                     equipe principal de engenharia
├── specialists/              agentes especializados
├── governance/               risco, segurança, performance, nuvem e FinOps
├── level6/                   motor autônomo de Issue para PR
├── production/               diagnóstico de produção somente leitura
├── evolution/                controle de autoevolução autônoma
└── evolution/skills.py       ciclo completo e runtime isolado de skills
```

## Knowledge Engine

O Knowledge Engine não depende de inferência do LLM para afirmar fatos sobre um repositório.
Ele coleta:

- linguagens;
- manifests;
- frameworks;
- persistência;
- filas;
- infraestrutura;
- símbolos AST;
- imports;
- dependências;
- arquitetura inferida com nível de confiança;
- histórico Git governado.

A prioridade das fontes é:

1. decisão humana explicitamente aprovada;
2. ADR aprovado;
3. regra de negócio documentada;
4. código de produção;
5. documentação;
6. histórico Git;
7. inferência de IA.

## Organização de engenharia

O REA transforma um objetivo estratégico na seguinte hierarquia:

```text
Objetivo estratégico
└── Iniciativas
    └── Projetos
        └── Unidades de trabalho
```

O planejador de portfólio valida dependências, calcula prioridade e executa uma pré-análise de
governança antes de publicar qualquer Issue.

A pontuação de prioridade é determinística:

```text
prioridade = valor_de_negócio × 5
           + alinhamento_estratégico × 4
           + urgência × 3
           - esforço × 2
```

Dependências sempre têm precedência sobre a pontuação.

## Equipe multiagente

O fluxo principal inclui:

- Engineering Manager;
- Tech Lead;
- Senior Developer;
- Code Reviewer.

Especialistas disponíveis:

- Backend;
- Frontend;
- Mobile;
- Banco de Dados;
- DevOps/SRE;
- QA;
- Segurança;
- Performance;
- Cloud Architecture;
- FinOps.

Os especialistas podem aumentar o risco detectado, mas não podem reduzir uma classificação
determinística existente.

## Level 6: Issue para Pull Request

O Level 6 executa uma Issue de ponta a ponta:

1. carrega a Issue;
2. cria o pacote de engenharia;
3. executa a governança especializada;
4. prepara um Git worktree isolado;
5. limita os arquivos que podem ser modificados;
6. solicita uma implementação ao agente desenvolvedor;
7. aplica as mudanças;
8. executa testes e validações em sandbox;
9. devolve falhas ao desenvolvedor para uma nova iteração;
10. obtém uma revisão independente;
11. cria commit;
12. envia a branch;
13. abre um Pull Request em modo draft.

O loop possui limite configurável entre uma e oito tentativas. O limite evita consumo infinito de
GPU e mudanças repetitivas sem progresso.

Exemplo:

```bash
rea issue run 42 \
  --repo owner/repository \
  --workspace /workspace/repository \
  --base main \
  --approve-rule git-push
```

## Governança

### Política de comandos

O arquivo `config/policies/commands.yaml` classifica comandos como:

- `allow`: permitido;
- `ask`: exige aprovação;
- `deny`: bloqueado;
- `cost_approval`: exige autorização específica de custo.

Operações como `sudo`, remoção destrutiva ampla, limpeza global do Docker e exclusões de
produção são bloqueadas.

### Motor de risco

O motor avalia:

- risco de segurança;
- risco de performance;
- impacto arquitetural;
- impacto financeiro;
- mudanças em produção;
- comandos solicitados;
- criticidade declarada.

Precedência:

1. escrita em produção declarada: bloqueada;
2. impacto financeiro: aprovação de custo;
3. risco crítico: aprovação humana;
4. risco alto: aprovação de Tech Lead;
5. risco médio: execução governada;
6. risco baixo: elegível para autonomia.

### Auditoria e proteção de segredos

Cada decisão e execução é registrada em `.rea/audit.jsonl`.

O REA remove valores com formato de:

- tokens;
- senhas;
- chaves de API;
- chaves privadas;
- credenciais de nuvem;
- arquivos sensíveis.

A remoção também funciona durante streaming para impedir que uma chave incompleta apareça
temporariamente na tela.

## Diagnóstico de produção

O módulo de produção coleta evidências de:

- logs;
- métricas;
- traces;
- deployments;
- histórico Git.

Ele correlaciona evidências, cria hipóteses de causa raiz, propõe remediações e produz postmortems.
Por padrão, nenhuma ação de escrita em produção é permitida.

```bash
rea incident analyze INC-001 \
  --service payments \
  --title "Aumento de erros 5xx" \
  --signals ./signals.jsonl
```

## Autoevolução autônoma

A versão 1.1 fecha o ciclo em torno do Level 6:

```text
Observar
→ detectar recorrências
→ criar hipótese mensurável
→ recuperar lições anteriores
→ construir candidato
→ medir baseline e candidato
→ rejeitar regressões
→ abrir PR
→ acompanhar CI
→ promover com segurança
→ observar o resultado
→ aprender
```

### Componentes

| Componente | Responsabilidade |
|---|---|
| `AuditObserver` | Normaliza eventos locais |
| `GitHubEvolutionClient` | Coleta falhas de CI e opera Issues/PRs |
| `OpportunityDetector` | Exige evidências recorrentes |
| `Level6CandidateBuilder` | Conecta hipóteses ao Level 6 |
| `GitWorktreeBenchmarkProvider` | Mede refs em worktrees descartáveis |
| `EvaluationEngine` | Compara baseline e candidato |
| `JsonEvolutionStore` | Persiste experimentos e lições |
| `EvolutionWorker` | Executa o ciclo contínuo |
| `ModelPromptOptimizer` | Seleciona candidatos por recompensa |
| `PromotionPolicy` | Decide se uma mudança pode avançar |
| `CanaryController` | Controla shadow, canário e rollback |

### Função de evolução

A avaliação considera:

- taxa de sucesso;
- sucesso na primeira tentativa;
- cobertura;
- regressões;
- intervenções humanas;
- falhas de segurança;
- latência;
- tempo de GPU.

Uma melhoria na pontuação total não pode compensar uma regressão crítica. Falhas adicionais de
segurança, regressões escapadas, queda relevante de cobertura ou queda na taxa de sucesso vetam
o candidato.

### Descobrir oportunidades

```bash
rea-evolve discover \
  --repo owner/repository \
  --audit .rea/audit.jsonl \
  --output hypotheses.json
```

O detector exige recorrência. Um evento isolado não se transforma automaticamente em mudança.

### Executar um experimento

Selecione uma hipótese produzida pelo comando anterior:

```bash
rea-evolve run hypothesis.json \
  --repo owner/repository \
  --workspace /workspace/repository \
  --approve-rule github-issue-create \
  --approve-rule git-push
```

O processo:

1. recupera lições relacionadas;
2. cria uma Issue com evidências;
3. executa o Level 6;
4. gera uma branch e um PR;
5. mede o baseline;
6. mede o candidato;
7. registra a decisão;
8. persiste uma nova lição.

### Worker contínuo

```bash
rea-evolve daemon \
  --repo owner/repository \
  --workspace /workspace/repository \
  --approve-rule github-issue-create \
  --approve-rule git-push \
  --approve-rule auto-merge \
  --max-experiments 2 \
  --max-gpu-minutes 60
```

O daemon:

- utiliza lock por repositório;
- persiste estado;
- retoma após reinicialização;
- impede hipóteses duplicadas;
- limita experimentos por dia;
- limita consumo de GPU;
- abre o circuit breaker após falhas consecutivas;
- mantém uma fila persistente de promoções;
- verifica novamente as PRs em cada ciclo;
- realiza merge apenas quando todos os gates estiverem satisfeitos.

Para executar uma única iteração por cron ou systemd:

```bash
rea-evolve daemon [opções] --once
```

### Seleção adaptativa de modelos

Configure os modelos candidatos:

```bash
export REA_EVOLUTION_MODELS="qwen3-coder:30b,qwen3:14b"
```

No PowerShell:

```powershell
$env:REA_EVOLUTION_MODELS="qwen3-coder:30b,qwen3:14b"
```

O otimizador UCB1 explora os modelos disponíveis e utiliza o resultado dos benchmarks como
recompensa. Ele altera apenas o roteamento do experimento; não modifica os pesos dos modelos.

### Promoção

Promoção manual governada:

```bash
rea-evolve promote EXPERIMENT_ID \
  --pr 123 \
  --repo owner/repository \
  --approve-rule auto-merge
```

A promoção automática falha de forma segura se:

- a branch `main` não estiver protegida;
- a CI não estiver verde;
- o PR possuir conflito;
- o candidato não superar o baseline;
- houver regressão crítica;
- houver impacto financeiro;
- o risco for alto ou crítico;
- a mudança alterar arquivos protegidos.

Arquivos protegidos incluem governança, políticas, CI e o próprio controlador de promoção. O
agente não pode modificar e aprovar sua própria autoridade no mesmo experimento.

### Shadow, canário e rollback

Copie e adapte:

```text
config/evolution-deployment.example.json
```

Execute:

```bash
rea-evolve canary CANDIDATE_REF \
  --config /caminho/seguro/deployment.json \
  --workspace /workspace/repository \
  --approve-rule production-canary \
  --max-percent 10
```

O controlador:

1. realiza shadow deployment;
2. verifica a saúde do shadow;
3. exige aprovação explícita para produção;
4. limita o canário entre 1% e 25%;
5. verifica a saúde do canário;
6. promove se estiver saudável;
7. executa rollback se houver degradação;
8. confirma que o rollback recuperou o ambiente.

Comandos de deployment são arrays de argumentos e nunca são executados por meio de shell.

## Ciclo autônomo de skills

A versão 1.2 permite ao REA identificar capacidades ausentes e criar novas skills sem incorporar
código não validado diretamente ao runtime.

O ciclo executado é:

```text
Observar lacunas recorrentes
→ gerar proposta com evidências
→ criar pacote skill.json + SKILL.md
→ validar esquema, caminhos e permissões
→ procurar padrões perigosos
→ executar testes em Docker isolado
→ calcular a integridade SHA-256
→ registrar uma versão imutável
→ ativar de forma governada
→ executar e coletar telemetria
→ comparar métricas com o SLO
→ manter, colocar em quarentena ou reverter
```

Cada manifesto define entrypoint, versão SemVer, dependências, permissões, comandos de validação,
taxa mínima de sucesso, quantidade mínima de observações e latência máxima.

### Descobrir lacunas

```bash
rea-skills discover --audit .rea/audit.jsonl
```

O detector utiliza eventos `skill.missing`, `intent.unsupported` e `tool.unavailable`. Por
padrão, são necessárias duas ocorrências independentes antes da criação de uma proposta.

### Gerar, validar e ativar automaticamente

```bash
rea-skills cycle \
  --generator-command rea-skill-generator \
  --minimum-occurrences 2 \
  --max-skills 1
```

O gerador configurado recebe a proposta e produz o pacote. A validação funcional acontece em
Docker sem rede, com filesystem somente leitura, capabilities removidas e limites de CPU, memória,
PIDs e tempo. A ausência do Docker ou uma validação inconclusiva bloqueia a ativação.

### Executar e observar

```bash
rea-skills invoke skill-exemplo --payload '{"entrada": "valor"}'
rea-skills record skill-exemplo --success --latency 2.4
rea-skills reconcile
rea-skills status
```

O runtime registra invocações, sucessos, falhas, latência, custo e falhas de segurança. Violações
de integridade colocam a versão em quarentena. Regressões de segurança ou SLO acionam rollback
para a versão anterior.

### Permissões protegidas

A ativação e a invocação exigem autorização explícita quando a skill solicita:

- leitura ou escrita de credenciais;
- rede externa;
- escrita em produção;
- exclusão de dados;
- merge no GitHub;
- alteração de governança;
- gerenciamento de outras skills.

Uma skill não pode conceder a si própria essas permissões.

## Persistência

Todos os dados duráveis ficam em `.rea/`:

| Caminho | Conteúdo |
|---|---|
| `.rea/knowledge/` | inventário dos repositórios |
| `.rea/rfcs/` | RFCs |
| `.rea/organization/` | roadmaps e portfólio |
| `.rea/work/` | pacotes e execuções Level 6 |
| `.rea/worktrees/` | worktrees isolados |
| `.rea/incidents/` | análises e postmortems |
| `.rea/evolution/experiments/` | experimentos de evolução |
| `.rea/evolution/lessons.jsonl` | memória episódica |
| `.rea/evolution/state.json` | orçamento, circuit breaker e filas |
| `.rea/skills/registry.json` | catálogo, versões, estados e métricas das skills |
| `.rea/skills/packages/` | pacotes de skills versionados e verificados por SHA-256 |
| `.rea/audit.jsonl` | auditoria geral |

O histórico da conversa permanece apenas na sessão atual. Decisões, planos, execuções,
experimentos e aprendizados são persistidos.

## Configuração

### Variáveis principais

- `REA_HOME`
- `REA_OLLAMA_URL`
- `REA_AUDIT_PATH`
- `REA_COMMAND_POLICY`
- `REA_MODEL_CONFIG`
- `REA_KNOWLEDGE_PATH`
- `REA_WORK_PATH`
- `REA_WORKTREE_PATH`
- `REA_EVOLUTION_MODELS`

### Arquivos

- `config/models.yaml`: modelos e rotas dos agentes;
- `config/policies/commands.yaml`: política de comandos;
- `config/evolution-benchmark.json`: benchmark da autoevolução;
- `config/evolution-deployment.example.json`: exemplo de integração de deployment;
- `config/skill-policy.example.json`: política de sandbox e permissões das skills.

## Referência da CLI

```bash
rea
rea chat
rea init .
rea init . --workspace
rea status
rea models status
rea policy check "git status"

rea repo scan .
rea repo list

rea org plan "objetivo" --repo owner/repository
rea org list
rea org show PLAN_ID
rea org publish PLAN_ID --approve-rule github-issue-create

rea team plan 42 --repo owner/repository
rea team review .rea/work/issue-42-plan.json
rea-agents capabilities

rea issue analyze 42 --repo owner/repository
rea issue run 42 --repo owner/repository --workspace .

rea production policy
rea production inspect --service service --signals signals.jsonl
rea incident analyze INC-001 --service service --title "Incidente" --signals signals.jsonl

rea sandbox run "pytest" --workspace .

rea-evolve discover --repo owner/repository
rea-evolve run hypothesis.json --repo owner/repository
rea-evolve daemon --repo owner/repository --workspace .
rea-evolve status
rea-evolve lessons --repo owner/repository
rea-evolve promote EXPERIMENT_ID --pr 123 --repo owner/repository
rea-evolve canary CANDIDATE_REF --config deployment.json

rea-skills discover --audit .rea/audit.jsonl
rea-skills cycle --generator-command rea-skill-generator
rea-skills install /caminho/da/skill
rea-skills invoke skill-exemplo --payload '{"entrada": "valor"}'
rea-skills record skill-exemplo --success --latency 2.4
rea-skills reconcile
rea-skills rollback skill-exemplo --reason "regressão"
rea-skills quarantine skill-exemplo --reason "violação de segurança"
rea-skills status
```

## Desenvolvimento e validação

```bash
python -m pytest
python -m ruff check .
python scripts/validate.py
```

O script de validação executa:

1. compilação do bytecode;
2. verificação de dependências;
3. Ruff;
4. Pytest.

## Ativação segura da autonomia

Antes de habilitar `auto-merge`:

1. mantenha o runner self-hosted online;
2. proteja a branch `main`;
3. proíba push direto;
4. torne a CI obrigatória;
5. configure CODEOWNERS para arquivos críticos;
6. utilize tokens com privilégio mínimo;
7. separe credenciais de leitura, PR, merge e produção;
8. configure métricas e SLOs reais;
9. valide shadow e rollback;
10. comece com um experimento de baixo risco por dia.

A aplicação verifica parte desses controles em tempo de execução, mas as regras do servidor
GitHub e as permissões externas continuam sendo a camada final de autoridade.

## Histórico de versões

| Versão | Evolução |
|---|---|
| V0.1 | CLI, modelos, sandbox, políticas, auditoria e PR draft |
| V0.2 | Knowledge Engine |
| V0.3 | primeira equipe multiagente |
| V0.4 | Level 6 Issue-to-PR |
| V0.5 | agentes especializados |
| V0.6 | diagnóstico de produção |
| V0.7 | governança avançada e motor de risco |
| V1.0 | organização de engenharia e portfólio |
| V1.1 | autoevolução mensurável, worker, promoção e canário |
| V1.2 | descoberta, geração, validação, runtime, telemetria e rollback de skills |

Os documentos de arquitetura estão em `docs/architecture/`. O manual operacional da
autoevolução está em `docs/autonomous-evolution-operations.md`.

## Estado de segurança

O REA foi construído para aumentar autonomia sem eliminar controle. Mesmo no modo mais autônomo:

- custos exigem autorização específica;
- mudanças críticas exigem aprovação;
- produção exige autorização própria;
- alterações de governança não podem se autoaprovar;
- regressões críticas bloqueiam promoção;
- a ausência de evidência bloqueia decisões;
- a ausência de CI verde bloqueia merge;
- a ausência de branch protection bloqueia auto-merge.
