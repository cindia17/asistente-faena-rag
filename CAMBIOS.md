# Registro de cambios

Qué se cambió en el proyecto, por qué y en qué archivos. El equipo (Cindia Maldonado e Isaac Román) trabajó el código en conjunto con Claude Code: la herramienta se usó como apoyo para programar, ejecutar las pruebas necesarias y detectar fallas; el equipo revisó todo junto con ella e hizo sus propios cambios. Este uso de IA está declarado en el Anexo A del informe.

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

### Evidencias (`evidencias/`)

Carpeta nueva, como pide la pauta ("evidencia de pruebas de software realizadas"):

- `pruebas_pytest.txt`: salida de `pytest -v` (35/35).
- `evaluacion_offline.txt` / `.json`: métricas sobre el conjunto de preguntas de prueba sin LLM (generador extractivo).
- `evaluacion_llm.txt` / `.json`: las mismas métricas con el LLM local (qwen3.5:0.8b en Ollama).
