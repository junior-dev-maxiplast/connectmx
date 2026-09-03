/* Logística de Pneus — interações das telas do módulo.
   Tudo é opcional: cada init só roda se os elementos da página existirem. */
(function () {
    "use strict";

    /* ------------------------------------------------------------ util -- */

    function $(selector, scope) {
        return (scope || document).querySelector(selector);
    }

    function $$(selector, scope) {
        return Array.prototype.slice.call((scope || document).querySelectorAll(selector));
    }

    function escapeHtml(value) {
        return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
        });
    }

    function clone(value) {
        try {
            return JSON.parse(JSON.stringify(value));
        } catch (error) {
            return value;
        }
    }

    function readJson(elementId, fallback) {
        var node = document.getElementById(elementId);
        if (!node) return fallback;
        try {
            return JSON.parse(node.textContent || "null") || fallback;
        } catch (error) {
            return fallback;
        }
    }

    /* ----------------------------------------------------------- modais -- */

    var openModalId = null;

    function openModal(id) {
        var modal = document.getElementById(id);
        var overlay = document.getElementById(id + "Overlay");
        if (!modal) return;

        modal.classList.remove("is-hidden");
        if (overlay) overlay.classList.remove("is-hidden");
        requestAnimationFrame(function () {
            modal.classList.add("is-open");
            if (overlay) overlay.classList.add("is-open");
        });
        openModalId = id;
    }

    function closeModal(id) {
        var modal = document.getElementById(id || openModalId);
        if (!modal) return;
        var overlay = document.getElementById(modal.id + "Overlay");

        modal.classList.remove("is-open");
        if (overlay) overlay.classList.remove("is-open");
        window.setTimeout(function () {
            modal.classList.add("is-hidden");
            if (overlay) overlay.classList.add("is-hidden");
        }, 180);
        if (openModalId === modal.id) openModalId = null;
    }

    function bindModalTriggers() {
        document.addEventListener("click", function (event) {
            var opener = event.target.closest("[data-tl-open]");
            if (opener) {
                event.preventDefault();
                openModal(opener.getAttribute("data-tl-open"));
                return;
            }
            var closer = event.target.closest("[data-tl-close]");
            if (closer) {
                event.preventDefault();
                closeModal(closer.getAttribute("data-tl-close") || undefined);
            }
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && openModalId) closeModal(openModalId);
        });
    }

    /* ----------------------------------------------------------- toasts -- */

    function initToasts() {
        $$(".tl-toast").forEach(function (toast, index) {
            window.setTimeout(function () {
                toast.style.transition = "opacity .3s ease, transform .3s ease";
                toast.style.opacity = "0";
                toast.style.transform = "translateY(6px)";
                window.setTimeout(function () {
                    toast.remove();
                }, 320);
            }, 5200 + index * 400);
        });
    }

    /* -------------------------------------------------- envio de formulário -- */

    function initSubmitGuards() {
        $$("form[data-tl-guard]").forEach(function (form) {
            form.addEventListener("submit", function (event) {
                var confirmMessage = form.getAttribute("data-tl-confirm");
                if (confirmMessage && !window.confirm(confirmMessage)) {
                    event.preventDefault();
                    return;
                }
                if (form.dataset.tlSubmitting === "1") {
                    event.preventDefault();
                    return;
                }
                form.dataset.tlSubmitting = "1";
                $$('button[type="submit"]', form).forEach(function (button) {
                    button.disabled = true;
                    if (!button.dataset.tlLabel) button.dataset.tlLabel = button.textContent;
                    button.textContent = button.getAttribute("data-tl-busy") || "Salvando...";
                });
            });
        });
    }

    /* ------------------------------------------------ posições do caminhão -- */

    function initTruckMap() {
        var map = $("[data-tl-map]");
        if (!map) return;

        var targets = readJson("tlTransferTargets", []);
        var slotModal = "tlSlotModal";
        var swapModal = "tlSwapModal";
        var todayIso = map.getAttribute("data-today") || "";

        var actionSelect = $("#tlSlotAction");
        var spareOption = $("#tlSlotActionSpare");
        var initialOption = $("#tlSlotActionInitial");
        var targetSelect = $("#tlSlotTarget");
        var submitButton = $("#tlSlotSubmit");
        var dateHelp = $("#tlSlotDateHelp");
        var kmHelp = $("#tlSlotKmHelp");

        function setFact(id, value) {
            var node = document.getElementById(id);
            if (node) node.textContent = value || "—";
        }

        function toggleFieldGroups() {
            if (!actionSelect) return;
            var mode = actionSelect.value;
            $$("[data-tl-when]").forEach(function (group) {
                var modes = (group.getAttribute("data-tl-when") || "").split(/\s+/);
                group.classList.toggle("is-hidden", modes.indexOf(mode) === -1);
            });

            /* DOT e sulco são obrigatórios, mas só nos modos que criam pneu:
               deixá-los `required` escondidos travaria o envio das outras ações. */
            $$("[data-tl-required-when]").forEach(function (field) {
                var modes = (field.getAttribute("data-tl-required-when") || "").split(/\s+/);
                field.required = modes.indexOf(mode) > -1;
            });

            /* Na carga inicial a data e o KM não são "de hoje": são o ponto de
               partida do pneu, então a ajuda contextual aparece. */
            var isInitial = mode === "initial_load";
            if (dateHelp) dateHelp.classList.toggle("is-hidden", !isInitial);
            if (kmHelp) kmHelp.classList.toggle("is-hidden", !isInitial);
            if (submitButton) {
                submitButton.textContent = isInitial ? "Registrar carga inicial" : "Registrar movimentação";
            }
        }

        function fillTargets(currentNumber) {
            if (!targetSelect) return;
            targetSelect.innerHTML =
                '<option value="">Selecione a posição</option>' +
                targets
                    .filter(function (item) {
                        return String(item.tire_number) !== String(currentNumber);
                    })
                    .map(function (item) {
                        return (
                            '<option value="' +
                            escapeHtml(item.tire_number) +
                            '">' +
                            escapeHtml(item.position_label) +
                            "</option>"
                        );
                    })
                    .join("");
        }

        function openSlot(button) {
            var data = button.dataset;
            var hasTire = data.hasTire === "1";
            var isSpare = data.isSpare === "1";

            setFact("tlSlotPosition", data.positionLabel);
            setFact("tlSlotTire", hasTire ? data.tireCode : "Vazia");
            setFact("tlSlotBrand", hasTire ? data.tireBrand : "—");
            setFact("tlSlotStatus", hasTire ? data.tireStatus : "Vazia");
            setFact("tlSlotRecap", hasTire ? data.tireRecap + "/3" : "—");
            setFact("tlSlotDot", hasTire ? data.tireDot : "—");
            setFact("tlSlotSize", hasTire ? data.tireSize : "—");
            setFact("tlSlotModel", hasTire ? data.tireModel : "—");
            setFact("tlSlotGroove", hasTire && data.tireGroove ? data.tireGroove + " mm" : "—");
            setFact(
                "tlSlotRun",
                data.lastRunKm || data.lastRunDays
                    ? [data.lastRunKm ? data.lastRunKm + " km" : null, data.lastRunDays ? data.lastRunDays + " dias" : null]
                          .filter(Boolean)
                          .join(" · ")
                    : "Sem registro"
            );
            setFact("tlSlotAge", data.tireAgeDays ? data.tireAgeDays + " dias na posição" : "Sem registro");

            var numberField = $("#tlSlotNumber");
            if (numberField) numberField.value = data.tireNumber;

            var dateField = $("#tlSlotDate");
            if (dateField) dateField.value = data.date || todayIso;
            var kmField = $("#tlSlotKm");
            if (kmField) kmField.value = data.km || "";
            var noteField = $("#tlSlotNote");
            if (noteField) noteField.value = "";
            var photoField = $("#tlSlotPhoto");
            if (photoField) photoField.value = "";
            var photoPreview = $("#tlSlotPhotoPreview");
            if (photoPreview) photoPreview.classList.remove("is-on");

            $$("#tlSlotAction option").forEach(function (option) {
                if (option === spareOption) {
                    option.hidden = !(isSpare && hasTire);
                    return;
                }
                if (option === initialOption) {
                    /* Carga inicial só faz sentido em posição vazia: com pneu
                       instalado, o caso é troca, não estado inicial. */
                    option.hidden = hasTire;
                    return;
                }
                var needsTire = ["move_to_stock", "send_current_to_retread", "discard_current"].indexOf(option.value) > -1;
                option.hidden = needsTire && !hasTire;
            });

            if (actionSelect) {
                actionSelect.value = hasTire ? "move_to_stock" : "install_stock";
                if (actionSelect.selectedOptions[0] && actionSelect.selectedOptions[0].hidden) {
                    actionSelect.value = "create_and_install";
                }
                toggleFieldGroups();
            }
            fillTargets(data.tireNumber);
            openModal(slotModal);
        }

        function openSwap(sourceButton, targetButton) {
            $("#tlSwapSource").value = sourceButton.dataset.tireNumber;
            $("#tlSwapTarget").value = targetButton.dataset.tireNumber;
            setFact("tlSwapSourcePosition", sourceButton.dataset.positionLabel);
            setFact("tlSwapSourceTire", sourceButton.dataset.hasTire === "1" ? sourceButton.dataset.tireCode : "Vazia");
            setFact("tlSwapTargetPosition", targetButton.dataset.positionLabel);
            setFact("tlSwapTargetTire", targetButton.dataset.hasTire === "1" ? targetButton.dataset.tireCode : "Vazia");

            var dateField = $("#tlSwapDate");
            if (dateField) dateField.value = todayIso;
            var kmField = $("#tlSwapKm");
            if (kmField) kmField.value = "";
            var noteField = $("#tlSwapNote");
            if (noteField) noteField.value = "";
            openModal(swapModal);
        }

        if (actionSelect) actionSelect.addEventListener("change", toggleFieldGroups);

        var stockSearch = $("#tlStockSearch");
        var stockSelect = $("#tlStockSelect");
        if (stockSearch && stockSelect) {
            stockSearch.addEventListener("input", function () {
                var term = stockSearch.value.trim().toLowerCase();
                $$("option", stockSelect).forEach(function (option) {
                    if (!option.value) return;
                    option.hidden = term ? option.textContent.toLowerCase().indexOf(term) === -1 : false;
                });
            });
        }

        var dragged = null;
        var slots = $$("[data-tl-slot]");

        function clearDropTargets() {
            slots.forEach(function (node) {
                node.classList.remove("is-drop-target");
            });
        }

        slots.forEach(function (button) {
            button.addEventListener("click", function () {
                openSlot(button);
            });

            if (button.dataset.hasTire === "1") {
                button.setAttribute("draggable", "true");
                button.addEventListener("dragstart", function (event) {
                    dragged = button;
                    button.classList.add("is-dragging");
                    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
                });
                button.addEventListener("dragend", function () {
                    button.classList.remove("is-dragging");
                    clearDropTargets();
                    dragged = null;
                });
            }

            button.addEventListener("dragover", function (event) {
                if (!dragged || dragged === button) return;
                event.preventDefault();
                button.classList.add("is-drop-target");
            });
            button.addEventListener("dragleave", function () {
                button.classList.remove("is-drop-target");
            });
            button.addEventListener("drop", function (event) {
                if (!dragged || dragged === button) return;
                event.preventDefault();
                button.classList.remove("is-drop-target");
                openSwap(dragged, button);
            });
        });

        toggleFieldGroups();
    }

    /* ------------------------------------------------------ editor modelo -- */

    function initModelEditor() {
        var canvas = $("#tlModelEditor");
        var hidden = $("#tlStructureJson");
        if (!canvas || !hidden) return;

        var summaryNodes = {
            axles: $("#tlSummaryAxles"),
            wheels: $("#tlSummaryWheels"),
            spares: $("#tlSummarySpares"),
            total: $("#tlSummaryTotal"),
        };

        var savedModels = readJson("tlModelStructures", {});
        var defaultStructure = readJson("tlDefaultStructure", [
            { left: [{ name: "DE" }], right: [{ name: "DD" }], spares: [] },
        ]);
        var structure = clone(defaultStructure);

        function normalize() {
            if (!Array.isArray(structure) || !structure.length) {
                structure = clone(defaultStructure);
            }
            var spares = [];
            structure = structure.map(function (axle) {
                var next = axle && typeof axle === "object" ? axle : {};
                (Array.isArray(next.spares) ? next.spares : []).forEach(function (spare, index) {
                    spares.push({ name: (spare && spare.name) || "Estepe " + (index + 1) });
                });
                next.left = (Array.isArray(next.left) ? next.left : []).map(function (wheel, index) {
                    return { name: (wheel && wheel.name) || "E" + (index + 1) };
                });
                next.right = (Array.isArray(next.right) ? next.right : []).map(function (wheel, index) {
                    return { name: (wheel && wheel.name) || "D" + (index + 1) };
                });
                next.spares = [];
                return next;
            });
            structure[0].spares = spares;
        }

        function chip(label, onRename, onRemove) {
            var wrap = document.createElement("span");
            wrap.className = "tl-chip";

            var text = document.createElement("span");
            text.textContent = label;
            wrap.appendChild(text);

            var rename = document.createElement("button");
            rename.type = "button";
            rename.title = "Renomear";
            rename.innerHTML = "&#9998;";
            rename.addEventListener("click", onRename);
            wrap.appendChild(rename);

            var remove = document.createElement("button");
            remove.type = "button";
            remove.title = "Remover";
            remove.innerHTML = "&times;";
            remove.addEventListener("click", onRemove);
            wrap.appendChild(remove);

            return wrap;
        }

        function addButton(label, onClick) {
            var button = document.createElement("button");
            button.type = "button";
            button.className = "tl-chip-add";
            button.textContent = label;
            button.addEventListener("click", onClick);
            return button;
        }

        function render() {
            normalize();
            canvas.innerHTML = "";

            structure.forEach(function (axle, axleIndex) {
                var row = document.createElement("div");
                row.className = "tl-editor-axle";

                ["left", "right"].forEach(function (side, sideIndex) {
                    var container = document.createElement("div");
                    container.className = "tl-editor-side is-" + side;

                    axle[side].forEach(function (wheel, wheelIndex) {
                        container.appendChild(
                            chip(
                                wheel.name,
                                function () {
                                    var name = window.prompt("Nome da posição", wheel.name);
                                    if (name === null) return;
                                    axle[side][wheelIndex].name = name.trim() || wheel.name;
                                    render();
                                },
                                function () {
                                    axle[side].splice(wheelIndex, 1);
                                    render();
                                }
                            )
                        );
                    });
                    container.appendChild(
                        addButton("+ roda", function () {
                            var prefix = side === "left" ? "E" : "D";
                            axle[side].push({ name: prefix + (axleIndex + 1) + (axle[side].length + 1) });
                            render();
                        })
                    );

                    if (sideIndex === 0) {
                        row.appendChild(container);

                        var axis = document.createElement("div");
                        axis.className = "tl-editor-axis";
                        var label = document.createElement("span");
                        label.textContent = "Eixo " + (axleIndex + 1);
                        axis.appendChild(label);
                        if (structure.length > 1) {
                            axis.appendChild(
                                addButton("remover", function () {
                                    structure.splice(axleIndex, 1);
                                    render();
                                })
                            );
                        }
                        row.appendChild(axis);
                    } else {
                        row.appendChild(container);
                    }
                });

                canvas.appendChild(row);
            });

            var sparesRow = document.createElement("div");
            sparesRow.className = "tl-editor-spares";
            var sparesLabel = document.createElement("span");
            sparesLabel.textContent = "Estepes";
            sparesRow.appendChild(sparesLabel);

            structure[0].spares.forEach(function (spare, spareIndex) {
                sparesRow.appendChild(
                    chip(
                        spare.name,
                        function () {
                            var name = window.prompt("Nome do estepe", spare.name);
                            if (name === null) return;
                            structure[0].spares[spareIndex].name = name.trim() || spare.name;
                            render();
                        },
                        function () {
                            structure[0].spares.splice(spareIndex, 1);
                            render();
                        }
                    )
                );
            });
            sparesRow.appendChild(
                addButton("+ estepe", function () {
                    structure[0].spares.push({ name: "Estepe " + (structure[0].spares.length + 1) });
                    render();
                })
            );
            canvas.appendChild(sparesRow);

            sync();
        }

        function sync() {
            hidden.value = JSON.stringify(structure);
            var wheels = structure.reduce(function (total, axle) {
                return total + axle.left.length + axle.right.length;
            }, 0);
            var spares = structure[0].spares.length;
            if (summaryNodes.axles) summaryNodes.axles.textContent = structure.length;
            if (summaryNodes.wheels) summaryNodes.wheels.textContent = wheels;
            if (summaryNodes.spares) summaryNodes.spares.textContent = spares;
            if (summaryNodes.total) summaryNodes.total.textContent = wheels + spares;
        }

        var addAxle = $("#tlAddAxle");
        if (addAxle) {
            addAxle.addEventListener("click", function () {
                var index = structure.length + 1;
                structure.push({
                    left: [{ name: index + "EE" }, { name: index + "EI" }],
                    right: [{ name: index + "DI" }, { name: index + "DE" }],
                    spares: [],
                });
                render();
            });
        }

        /* Um único editor atende tanto "novo modelo" quanto a edição de cada card:
           trocamos a estrutura carregada e reabrimos o modal. */
        var idField = $("#tlModelId");
        var nameField = $("#tlModelName");
        var titleNode = $("#tlModelModalTitle");
        var submitButton = $("#tlModelSubmit");

        function loadModel(modelId) {
            var saved = modelId ? savedModels[String(modelId)] : null;

            structure = clone(saved ? saved.structure : defaultStructure);
            if (idField) idField.value = saved ? modelId : "";
            if (nameField) nameField.value = saved ? saved.name : "";
            if (titleNode) titleNode.textContent = saved ? "Editar modelo" : "Novo modelo";
            if (submitButton) submitButton.textContent = saved ? "Salvar modelo" : "Criar modelo";

            render();
            openModal("tlModelModal");
            if (nameField) nameField.focus();
        }

        document.addEventListener("click", function (event) {
            if (event.target.closest("[data-tl-model-new]")) {
                event.preventDefault();
                loadModel(null);
                return;
            }
            var editButton = event.target.closest("[data-tl-model-edit]");
            if (editButton) {
                event.preventDefault();
                loadModel(editButton.getAttribute("data-tl-model-edit"));
            }
        });

        var modal = $("#tlModelModal");
        var autoload = modal && modal.getAttribute("data-tl-autoload");
        if (autoload) {
            loadModel(autoload === "new" ? null : autoload);
        } else {
            render();
        }
    }

    /* --------------------------------------------------- cadastro em lote -- */

    function initBatchForm() {
        var form = $("#tlTireCreateForm");
        if (!form) return;

        var modeInputs = $$('input[name="batch_mode"]', form);
        var previewCount = $("#tlBatchCount");
        var previewList = $("#tlBatchPreview");
        var previewPanel = $("[data-tl-check-url]");
        var totalsBox = $("#tlBatchTotals");
        var newCountNode = $("#tlBatchNewCount");
        var totalValueNode = $("#tlBatchTotalValue");
        var warningNode = $("#tlBatchWarning");
        var unitValueField = $("#tlTireValue");

        var checkUrl = previewPanel && previewPanel.getAttribute("data-tl-check-url");
        var takenSerials = {};
        var checkTimer = null;
        var lastChecked = "";

        function parseMoney(raw) {
            var text = String(raw || "").replace(/R\$/g, "").replace(/\s/g, "");
            if (!text) return null;
            /* "1.250,00" (pt-BR) vira "1250.00"; "1250.00" segue como está. */
            if (text.indexOf(",") > -1) {
                text = text.replace(/\./g, "").replace(",", ".");
            }
            var value = Number(text);
            return isFinite(value) ? value : null;
        }

        function formatMoney(value) {
            return "R$ " + value.toFixed(2).replace(".", ",").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
        }

        function csrfToken() {
            var field = form.querySelector('input[name="csrfmiddlewaretoken"]');
            return field ? field.value : "";
        }

        function checkDuplicates(list) {
            if (!checkUrl || !list.length) {
                takenSerials = {};
                return;
            }
            var signature = list.join("|");
            if (signature === lastChecked) return;
            lastChecked = signature;

            window.clearTimeout(checkTimer);
            checkTimer = window.setTimeout(function () {
                fetch(checkUrl, {
                    method: "POST",
                    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
                    body: JSON.stringify({ serials: list }),
                })
                    .then(function (response) {
                        return response.ok ? response.json() : null;
                    })
                    .then(function (payload) {
                        if (!payload || payload.status !== "ok") return;
                        takenSerials = {};
                        payload.taken.forEach(function (serial) {
                            takenSerials[String(serial).toLowerCase()] = true;
                        });
                        paint(list);
                    })
                    .catch(function () {
                        /* Sem rede a prévia continua útil, só não marca duplicados. */
                    });
            }, 350);
        }

        function activeMode() {
            var checked = modeInputs.filter(function (input) {
                return input.checked;
            })[0];
            return checked ? checked.value : "single";
        }

        function tokens() {
            var mode = activeMode();
            if (mode === "generate") {
                var prefix = ($("#tlBatchPrefix").value || "").trim();
                var start = Number($("#tlBatchStart").value || 0);
                var quantity = Number($("#tlBatchQuantity").value || 0);
                var pad = Math.max(0, Math.min(Number($("#tlBatchPad").value || 0), 8));
                if (!prefix || !quantity || quantity < 1) return [];
                var list = [];
                for (var index = 0; index < Math.min(quantity, 500); index += 1) {
                    var value = String(start + index);
                    list.push(prefix + (pad ? value.padStart(pad, "0") : value));
                }
                return list;
            }
            if (mode === "paste") {
                var seen = {};
                return ($("#tlBatchList").value || "")
                    .split(/\r?\n|,|;/)
                    .map(function (item) {
                        return item.trim();
                    })
                    .filter(function (item) {
                        if (!item || seen[item.toLowerCase()]) return false;
                        seen[item.toLowerCase()] = true;
                        return true;
                    });
            }
            var single = ($("#tlSerialNumber").value || "").trim();
            return single ? [single] : [];
        }

        function paint(list) {
            var duplicates = list.filter(function (item) {
                return takenSerials[item.toLowerCase()];
            });
            var pending = list.length - duplicates.length;

            if (previewCount) previewCount.textContent = list.length + (list.length === 1 ? " pneu" : " pneus");

            if (previewList) {
                previewList.innerHTML = list.length
                    ? list
                          .slice(0, 12)
                          .map(function (item) {
                              var isTaken = takenSerials[item.toLowerCase()];
                              return (
                                  '<span class="tl-badge' +
                                  (isTaken ? " is-danger" : "") +
                                  '"' +
                                  (isTaken ? ' title="Já existe no cadastro"' : "") +
                                  ">" +
                                  escapeHtml(item) +
                                  "</span>"
                              );
                          })
                          .join("") +
                      (list.length > 12 ? '<span class="tl-badge">+' + (list.length - 12) + "</span>" : "")
                    : '<span class="tl-empty-inline">Nenhum número informado ainda.</span>';
            }

            var unitValue = unitValueField ? parseMoney(unitValueField.value) : null;
            if (totalsBox) totalsBox.hidden = !list.length;
            if (newCountNode) newCountNode.textContent = pending;
            if (totalValueNode) {
                totalValueNode.textContent =
                    unitValue && pending
                        ? pending + " × " + formatMoney(unitValue) + " = " + formatMoney(unitValue * pending)
                        : "—";
            }

            if (warningNode) {
                warningNode.hidden = !duplicates.length;
                warningNode.textContent = duplicates.length
                    ? duplicates.length +
                      (duplicates.length === 1 ? " número já existe" : " números já existem") +
                      " no cadastro e será" +
                      (duplicates.length === 1 ? "" : "m") +
                      " ignorado" +
                      (duplicates.length === 1 ? "" : "s") +
                      "."
                    : "";
            }
        }

        function render() {
            var mode = activeMode();
            $$("[data-tl-batch-panel]", form).forEach(function (panel) {
                panel.classList.toggle("is-hidden", panel.getAttribute("data-tl-batch-panel") !== mode);
            });
            $$("[data-tl-batch-card]", form).forEach(function (card) {
                var input = $('input[name="batch_mode"]', card);
                card.classList.toggle("is-active", !!input && input.value === mode);
            });

            var list = tokens();
            paint(list);
            checkDuplicates(list);
        }

        modeInputs.forEach(function (input) {
            input.addEventListener("change", render);
        });
        $$("input, textarea", form).forEach(function (field) {
            field.addEventListener("input", render);
        });
        render();
    }

    /* ------------------------------------------------------ foto anexada -- */

    function initPhotoInputs() {
        $$("[data-tl-photo]").forEach(function (input) {
            var preview = document.getElementById(input.getAttribute("data-tl-photo"));
            var image = preview ? $("img", preview) : null;
            if (!preview || !image) return;

            input.addEventListener("change", function () {
                var file = input.files && input.files[0];
                if (image.src.indexOf("blob:") === 0) URL.revokeObjectURL(image.src);
                if (!file) {
                    preview.classList.remove("is-on");
                    image.removeAttribute("src");
                    return;
                }
                image.src = URL.createObjectURL(file);
                preview.classList.add("is-on");
            });
        });
    }

    /* --------------------------------------------------- descarte de pneu -- */

    function initDiscardModal() {
        var modal = $("#tlDiscardModal");
        if (!modal) return;

        var idField = $("#tlDiscardTireId");
        var noteField = $("#tlDiscardNote");
        var photoField = $("#tlDiscardPhoto");
        var preview = $("#tlDiscardPhotoPreview");

        document.addEventListener("click", function (event) {
            var trigger = event.target.closest("[data-tl-discard]");
            if (!trigger) return;
            event.preventDefault();

            if (idField) idField.value = trigger.getAttribute("data-tire-id") || "";
            var tireNode = $("#tlDiscardTire");
            if (tireNode) tireNode.textContent = trigger.getAttribute("data-tire-serial") || "—";
            var brandNode = $("#tlDiscardBrand");
            if (brandNode) brandNode.textContent = trigger.getAttribute("data-tire-brand") || "—";

            /* O modal é único na página: limpar aqui evita levar o motivo e a
               foto de um pneu para o descarte do próximo. */
            if (noteField) noteField.value = "";
            if (photoField) photoField.value = "";
            if (preview) preview.classList.remove("is-on");

            openModal("tlDiscardModal");
        });
    }

    /* ------------------------------------------ movimentações por pneu -- */

    function initTireTracks() {
        var panel = $("[data-tl-tire-tracks]");
        if (!panel) return;

        var chips = $$("[data-tl-track-filter]", panel);
        var tracks = $$("[data-tl-track]", panel);
        if (!chips.length) return;

        function apply(tireId) {
            chips.forEach(function (chip) {
                chip.classList.toggle("is-active", chip.getAttribute("data-tl-track-filter") === tireId);
            });
            tracks.forEach(function (track) {
                var mine = track.getAttribute("data-tl-track");
                track.classList.toggle("is-hidden", !!tireId && mine !== tireId);
            });
            /* O filtro também acende a posição no mapa, para ligar a lista ao
               desenho do caminhão. */
            $$("[data-tl-slot]").forEach(function (slot) {
                slot.classList.toggle("is-highlighted", !!tireId && slot.dataset.tireId === tireId);
            });
        }

        chips.forEach(function (chip) {
            chip.addEventListener("click", function () {
                var value = chip.getAttribute("data-tl-track-filter") || "";
                /* Clicar de novo no pneu já filtrado volta para "Todos". */
                apply(chip.classList.contains("is-active") ? "" : value);
            });
        });

        /* Clicar numa posição do mapa filtra a lista pelo pneu daquela posição. */
        $$("[data-tl-slot]").forEach(function (slot) {
            slot.addEventListener("click", function () {
                var tireId = slot.dataset.tireId;
                if (tireId) apply(tireId);
            });
        });
    }

    /* -------------------------------------------- confirmar exclusao -- */

    function initConfirmDelete() {
        var modal = $("#tlConfirmModal");
        if (!modal) return;

        var form = $("#tlConfirmForm");
        var fieldsBox = $("#tlConfirmFields");
        var warningNode = $("#tlConfirmWarning");

        document.addEventListener("click", function (event) {
            var trigger = event.target.closest("[data-tl-confirm-delete]");
            if (!trigger) return;
            event.preventDefault();

            form.setAttribute("action", trigger.getAttribute("data-action") || "");

            var kindNode = $("#tlConfirmKind");
            if (kindNode) kindNode.textContent = trigger.getAttribute("data-kind") || "Registro";
            var targetNode = $("#tlConfirmTarget");
            if (targetNode) targetNode.textContent = trigger.getAttribute("data-target") || "—";

            var warning = trigger.getAttribute("data-warning") || "";
            if (warningNode) {
                warningNode.textContent = warning;
                warningNode.hidden = !warning;
            }

            /* Os campos do POST vêm do botão. Montados como nós, nunca como
               HTML, para que um número de série com aspas não escape daqui. */
            fieldsBox.textContent = "";
            var payload = {};
            try {
                payload = JSON.parse(trigger.getAttribute("data-fields") || "{}") || {};
            } catch (error) {
                payload = {};
            }
            Object.keys(payload).forEach(function (name) {
                var input = document.createElement("input");
                input.type = "hidden";
                input.name = name;
                input.value = payload[name] == null ? "" : String(payload[name]);
                fieldsBox.appendChild(input);
            });

            /* O guard trava o form depois do primeiro envio; como o modal é
               reaproveitado, ele volta a aceitar envio a cada abertura. */
            form.dataset.tlSubmitting = "";
            $$('button[type="submit"]', form).forEach(function (button) {
                button.disabled = false;
                if (button.dataset.tlLabel) button.textContent = button.dataset.tlLabel;
            });

            openModal("tlConfirmModal");
        });
    }

    /* ------------------------------------------------------------- boot -- */

    document.addEventListener("DOMContentLoaded", function () {
        bindModalTriggers();
        initToasts();
        initSubmitGuards();
        initTruckMap();
        initModelEditor();
        initBatchForm();
        initPhotoInputs();
        initDiscardModal();
        initTireTracks();
        initConfirmDelete();
    });

    window.TireLogistics = { openModal: openModal, closeModal: closeModal };
})();
