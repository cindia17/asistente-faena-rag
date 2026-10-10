# Registro de cambios

Qué se cambió en el proyecto, por qué y en qué archivos. El equipo (Cindia Maldonado e Isaac Román) trabajó el código en conjunto con Claude Code: la herramienta se usó como apoyo para programar, ejecutar las pruebas necesarias y detectar fallas; el equipo revisó todo junto con ella e hizo sus propios cambios. Este uso de IA está declarado en el Anexo A del informe.

---

## 2026-10-09

### Configuración con archivo `.env`

Hasta ahora toda la configuración tenía valores por defecto en `config.py` y se cambiaba con variables de la terminal. Con LangSmith aparece el primer secreto del proyecto (la API key), y escribirla en cada terminal es incómodo. Se agrega soporte para un archivo `.env`, cuidando que la clave no termine en el repositorio.

| Archivo | Cambio |
|---|---|
| `.gitignore` | Excluye `.env` (y variantes), salvo la plantilla `.env.example` |
| `.env.example` | Plantilla sin secretos con las variables de LangSmith y del modo de ejecución |
| `requirements.txt` | Agrega `python-dotenv` |
| `src/asistente/config.py` | `cargar_env()`: lee el `.env` al iniciar sin pisar las variables de la terminal; no se carga durante `pytest`; si falta `python-dotenv`, el asistente funciona igual |
| `src/asistente/observabilidad.py` | Importa `config` para que el `.env` esté cargado antes de leer las variables de LangSmith |
| `README.md` | Cómo usar el `.env` |

---

## 2026-10-03

### Métricas por respuesta y observabilidad con LangSmith

Se agregan métricas de **rendimiento** (tiempo total, recuperación, generación y documentos recuperados) y de **calidad** (fidelidad, relevancia y precisión del contexto) en **cada respuesta**, y la integración con **LangSmith** para observabilidad, monitoreo y evaluación.

| Archivo | Cambio |
|---|---|
| `src/asistente/metricas.py` (nuevo) | Calcula las métricas de cada respuesta. Fidelidad: la del Verificador. Relevancia: conceptos núcleo de la pregunta presentes en la respuesta (con bge-m3 se complementa con similitud semántica). Precisión del contexto: fórmula de RAGAS (promedio de precision@k en las posiciones útiles). |
| `src/asistente/observabilidad.py` (nuevo) | Integración opcional con LangSmith: traza por consulta (grafo de LangGraph, recuperación como *retriever*, LLM, SAP PM y verificador), métricas como *feedback* y filtros de lo que se envía. Solo se activa con `LANGSMITH_TRACING=true` y `LANGSMITH_API_KEY`. |
| `src/asistente/grafo.py` | `consultar` devuelve `metricas` y `traza_langsmith`; las métricas quedan también en la traza JSONL. |
| `src/asistente/recuperacion.py`, `llm.py`, `verificador.py`, `herramientas.py` | Cada paso queda trazado en LangSmith (sin efecto si está desactivado). |
| `app.py` | Tarjetas de rendimiento y calidad bajo cada respuesta, promedios y gráfico de tiempos en el panel de supervisor, estado de LangSmith en la barra lateral. |
| `src/asistente/cli.py` | `--detalle` muestra las métricas y el id de la traza. |
| `eval/evaluadores.py` (nuevo) | Evaluadores compartidos por la evaluación local y la de LangSmith, para que ambas midan lo mismo. |
| `eval/evaluar_langsmith.py` (nuevo) | Sube el conjunto dorado como dataset y crea experimentos en LangSmith. Con `--local` imprime la tabla de métricas por respuesta sin enviar nada. |
| `eval/evaluar.py` | Informa además relevancia, precisión del contexto, tiempos de recuperación y generación y documentos recuperados (informativas, sin meta: el criterio de aprobación no cambia). |
| `tests/test_metricas.py` (nuevo) | 15 pruebas: fórmula de la precisión del contexto, relevancia, métricas en cada respuesta y en la abstención, feedback y privacidad. |
| `requirements.txt` | Se agrega `langsmith`. |

### Falla de privacidad detectada al integrar LangSmith y cómo se corrigió

| Qué pasaba | Causa | Corrección |
|---|---|---|
| Al activar LangSmith, el **token SSO** y la memoria de la sesión se habrían enviado en cada traza. | LangGraph registra el estado completo del grafo, y el token y la memoria viajaban en ese estado. El nombre de usuario también. | El estado solo lleva un id de sesión opaco; el token y la memoria quedan en memoria del proceso mientras dura la consulta (`AsistenteFaena._sesiones`). El usuario va seudonimizado desde el primer nodo y la herramienta de SAP no envía el token. Prueba: `test_el_token_no_entra_al_estado_del_grafo`. |

### Ajuste de la métrica de relevancia

La primera versión contaba verbos auxiliares ("¿cuáles **son** los pasos…?", "¿qué accidentes **han** ocurrido…?") como conceptos de la pregunta y bajaba el puntaje sin motivo. Se excluyen. La relevancia léxica sigue sin reconocer paráfrasis ("capacitación" / "instrucción"): con bge-m3 se complementa con similitud semántica, igual que el Verificador.

### Verificación

Las 50 pruebas (35 anteriores + 15 nuevas) pasaron en un entorno de desarrollo sin acceso a PyPI, con sustitutos locales de `langgraph`, `rank-bm25` y `pytest` y el embedder de respaldo. La integración con LangSmith se comprobó con un SDK simulado (qué se envía, que el token no sale y que el feedback llega a la traza correcta), no contra el servicio real.

**Pendiente en el equipo del proyecto:** `pytest -v`, `python eval/evaluar.py --offline` con Ollama (bge-m3), y una corrida con LangSmith activo para confirmar las trazas y el experimento en la interfaz.

---

## 2026-09-27

### Informe (`informe/Informe Chuquicamata (corregido).docx`)

| Cambio | Motivo |
|---|---|
| Enlace real al repositorio (`https://github.com/cindia17/asistente-faena-rag`) como hipervínculo en la portada. | La pauta exige entregar el enlace al repositorio; antes decía `<usuario>`. |
| Se restauró el título **Referencias** y las referencias de Chen et al. (2024), Cormack et al. (2009) y Es et al. (2024). | Se habían borrado por error en una corrección anterior, aunque se citan en las secciones 3.2, 3.3 y 5.1 (APA exige que toda cita tenga su referencia). La referencia de Gao et al. (2023) había quedado pegada a la reflexión de Isaac. |
| Se agregó la referencia APA de la herramienta de IA (Anthropic, 2026). | La pauta indica que todo uso de IA debe citarse según https://bibliotecas.duoc.cl/ia. |
| Se reescribió el **Anexo A** (declaración de uso de IA): herramienta, en qué se usó, ejemplo de instrucción y cómo se validó. | La pauta pide declarar qué herramientas de IA se usaron y cómo se aplicaron. |
| Los títulos 6.1 y 6.2 quedaron con el mismo estilo que los demás subtítulos. | Consistencia de formato. |
| Al final de 5.1 se agregó una frase que remite al nuevo **Anexo B. Resultados del prototipo** (tabla con métrica, meta, primera evaluación y resultado final). | El informe no mostraba los resultados medidos del prototipo; es evidencia que respalda el diseño (IE5). Se dejó en un anexo para que el cuerpo siga dentro de las 5 páginas. |
| En la tabla 5.2, fila "Conectividad", se agregó: "si el modelo no responde, respuesta con citas textuales". | Ahora está implementado en el prototipo (ver Código). |

**Aportes del equipo integrados al informe** (escritos por Cindia e Isaac en su copia de trabajo): reflexiones individuales 6.1 y 6.2; tilde en "Cristian Andrés"; OE1 "Hoy: 0 %"; "antepón" en la regla 5 del prompt; fila "Conectividad" de 5.2 (modelo ligero en talleres y el de 70B centralizado); referencias de Chen et al. (2024), Es et al. (2024) y Gao et al. (2023) actualizadas con DOI. Como no se usaron otras herramientas de IA, se quitó la línea pendiente del Anexo A. El informe ya no tiene textos pendientes.

Revisión final: en 1.1, la cita "SERNAGEOMIN (2026)" pasa a "Servicio Nacional de Geología y Minería (SERNAGEOMIN, 2026)", porque en APA 7 la primera cita de un autor institucional con sigla lleva el nombre completo, que es como aparece en Referencias. Se verificó que todas las citas del texto tienen su referencia y viceversa.

No se modificaron el análisis del caso, las justificaciones técnicas, las conclusiones ni las reflexiones individuales (la pauta prohíbe usar IA en ellas).

### Código (fallas lógicas corregidas)

| Archivo | Falla | Corrección |
|---|---|---|
| `src/asistente/grafo.py` | En modo offline, el "reintento" volvía a generar exactamente el mismo texto extractivo y lo verificaba otra vez. | Solo se reintenta si el primer borrador vino del LLM. |
| `src/asistente/grafo.py` | Si no se encontraba el procedimiento de bloqueo, igual se descartaba el 5.º fragmento. | Solo se reemplaza un fragmento cuando sí se encontró el de bloqueo. |
| `src/asistente/agentes.py` | El generador extractivo no respetaba el límite de 200 palabras (regla R8) y agregaba órdenes de SAP PM sin relación con la pregunta. | Presupuesto de palabras y solo órdenes pertinentes (todas si se pide el historial). |
| `src/asistente/recuperacion.py` | Al buscar "PETS-SEG-001 …", el reranker dejaba primero otro documento, porque no premiaba el identificador exacto. | Bono al documento nombrado por su identificador. |
| `src/asistente/ingesta.py` | Si una sección era solo una orden inyectada, se borraba sin dejar registro de que el documento era sospechoso. | Todo el documento queda marcado como sospechoso (visible en la traza y en el resumen del índice). |
| `src/asistente/trazas.py` | La carpeta de trazas se fijaba al importar el módulo, así que cambiarla en la configuración no tenía efecto. | Se lee de `config` en cada llamada. |
| `src/asistente/verificador.py` | La fidelidad podía contar afirmaciones duplicadas o dar un valor negativo al combinarse con el verificador LLM. | Se calcula antes de combinar y sin duplicados. |
| `.gitignore` | Se versionaban archivos generados (`*.egg-info/`, reportes registrados localmente). | Se ignoran. |

### Errores detectados por las pruebas y cómo se corrigieron

La primera ejecución completa de `pytest` (con Ollama y bge-m3) dio **29 de 32**. Estas fueron las 3 fallas:

| Prueba que falló | Qué mostró | Causa | Cómo se corrigió |
|---|---|---|---|
| `test_identificador_exacto_por_bm25` | Al buscar "PETS-SEG-001 retiro del bloqueo", el primer resultado era PETS-MEC-012. | BM25 sí encontraba el documento, pero el reranker (60 % similitud semántica + 40 % cobertura) no tomaba en cuenta el identificador exacto. | Bono de +0,2 al documento cuyo identificador aparece en la consulta (`recuperacion.py`). |
| `test_respuesta_dentro_de_limite` | La respuesta del ejemplo del camión 14 tenía 273 palabras (límite de la prueba: 260). | El generador extractivo no controlaba el largo y agregaba una orden de "presión de aceite" que no tenía relación con una consulta por temperatura. | Presupuesto de palabras según `MAX_PALABRAS` y filtro de órdenes pertinentes (`agentes.py`). |
| `test_documento_malicioso_saneado_al_indexar` | El documento del proveedor con la orden inyectada no quedaba marcado como sospechoso. | La sección maliciosa se eliminaba completa y, con ella, la marca de sospecha. | La marca se aplica a todo el documento (`ingesta.py`). |

Las otras fallas de la tabla anterior (reintento repetido, recorte de fragmentos, trazas, fidelidad) no hacían fallar ninguna prueba: se detectaron al revisar el código y se corrigieron igual.

Después de las correcciones se borró el índice (`data/indice/`) para reconstruirlo con la nueva ingesta y se volvieron a ejecutar todas las pruebas: **32 de 32 pasan** (`pytest -v`, ver `evidencias/pruebas_pytest.txt`).

### Errores detectados en la evaluación y cómo se corrigieron

La primera evaluación offline (`python eval/evaluar.py --offline`, 26 preguntas) **no cumplía dos metas**: context recall@5 = 0,794 (meta 0,85) y abstención correcta = 0,75 (meta 0,90). El asistente respondía "No tengo respaldo documental" a 6 preguntas que sí tienen respuesta en el corpus.

| Casos | Causa | Corrección |
|---|---|---|
| N03 "¿Qué hago si alguien ingiere refrigerante?", D07 "¿Qué accidentes han ocurrido con la correa…?" | El clasificador no reconocía "ingiere" ni "accidentes" como temas de seguridad. La consulta iba al agente de Mantenimiento, cuyo filtro **excluye** la hoja de seguridad y el resumen de incidentes, justo los documentos con la respuesta. | Se agregan `ingier`, `accident`, `lesion` y `autoriz` a las palabras de procedimientos (`agentes.py`). |
| N07 "¿Quién autoriza…?" | Se clasificaba como fuera de alcance y no aparecía PETS-SEG-001 (sección Autorización). | Igual que el caso anterior (`autoriz`). |
| D03 "¿…en qué mezcla?", D08 "¿Qué partes… alimenta el sistema hidráulico?" | El manual está en inglés y el glosario no tenía "mezcla → mixture", "alimenta → supplies" ni "sistema → system". | Términos agregados al glosario (`texto.py`). |
| D06 "¿Qué pasa si la falla… se repite en menos de 90 días?" | Palabras como "pasa", "menos", "usa" o "partes" se contaban como núcleo de la pregunta y bajaban la cobertura. | Se agregan a las palabras genéricas (`agentes.py`). |
| D05 "¿Qué fallas ha tenido el camión 14?" | La respuesta está en SAP PM (herramienta), pero el sistema exigía que la respaldaran documentos. | Si se pide el historial y SAP PM lo entrega, la respuesta se respalda en ese dato exacto (`grafo.py`, `agentes.py`). |

Resultado después de corregir: **26 de 26 casos correctos** y todas las metas cumplidas (fidelidad 1,0; recall@5 1,0; abstención correcta 1,0; resistencia a manipulación 1,0; 0 usos de revisiones superadas; latencia p95 7,0 s < 8 s). Las 32 pruebas siguen pasando.

### Error detectado al evaluar con el LLM y cómo se corrigió

| Qué pasó | Causa | Corrección |
|---|---|---|
| La evaluación con el LLM local se detuvo con `ReadTimeout`: el modelo no respondió en 300 s y **toda la consulta se cayó con un error** (lo mismo habría pasado en la demo). | `generar_con_llm` no manejaba el caso de que Ollama no respondiera (caído o saturado por otro proceso). | Si el LLM no responde, la respuesta se arma con el generador extractivo y se avisa al usuario (`grafo.py`), en línea con RNF-03 (conectividad limitada). El tiempo de espera ahora es configurable con `TIMEOUT_LLM` (`config.py`, `llm.py`). Nueva prueba: `test_llm_sin_respuesta_usa_generador_extractivo`. Resultado: **33 de 33 pruebas pasan**. |

### Problemas vistos al probar la demo y cómo se corrigieron

| Qué pasó | Causa | Corrección |
|---|---|---|
| Al escribir "hola asistente", respondía "No tengo respaldo documental…". | Un saludo se trataba como una pregunta sin respaldo. | Nueva intención `saludo`: el asistente se presenta y sugiere preguntas de ejemplo (`agentes.py`, `grafo.py`). Prueba: `test_saludo_presenta_al_asistente`. |
| Ante "¿Qué hago si alguien ingiere refrigerante?", la respuesta partía con "Primero hay que bloquear las energías del equipo". | La regla R5 (bloqueo primero) se aplicaba a toda consulta de mantenimiento, aunque nadie fuera a intervenir un equipo. | R5 se aplica solo si la consulta implica tocar el equipo (revisar, intervenir, cambiar, abrir, inspeccionar…) (`implica_intervencion` en `agentes.py`). Prueba: `test_bloqueo_solo_si_se_interviene_el_equipo`. |

Resultado: **35 de 35 pruebas pasan** y la evaluación offline sigue en 26/26.

### Evaluación con el LLM local (resultado y análisis)

`python eval/evaluar.py` con qwen3.5:0.8b en Ollama (solo CPU): fidelidad, recall@5, abstención correcta y resistencia a manipulación = 1,0, pero **latencia p50 175 s y p95 302 s** (no cumple RNF-01 en este equipo).

Al revisar las trazas: de 19 respuestas generadas, el verificador **rechazó 17 borradores del modelo** y los reemplazó con el generador extractivo; 1 borrador no llegó a tiempo (se usó el respaldo) y solo 1 pasó directo. Ejemplo: ante "¿Quién puede retirar un candado de bloqueo?", el modelo tenía en su contexto "Solo quien instaló el candado puede retirarlo" (PETS-SEG-001) y aun así respondió "No tengo respaldo documental", sin citas.

Conclusión: **no es una falla del código**. El verificador y el reintento hicieron su trabajo; la limitación es del modelo pequeño en CPU (ya prevista en el README). Se reportó en el Anexo B del informe. Para la demo se recomienda el modo offline (`MODO_LLM=offline`, unos 5 s por respuesta); en producción, el diseño usa el modelo de 70B con vLLM.

### Evidencias (`evidencias/`)

Carpeta nueva, como pide la pauta ("evidencia de pruebas de software realizadas"):

- `pruebas_pytest.txt`: salida de `pytest -v` (35/35).
- `evaluacion_offline.txt` / `.json`: métricas sobre el conjunto de preguntas de prueba sin LLM (generador extractivo).
- `evaluacion_llm.txt` / `.json`: las mismas métricas con el LLM local (qwen3.5:0.8b en Ollama).
