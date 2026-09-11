/* ==========================================================================
   Contagem de pallets — leitura pela câmera do celular.

   Um passo por tela: matrícula -> "Ler Pallet" -> leitura -> conferência dos
   campos separados -> "Salvar Leitura" ou "Cancelar".

   A decodificação usa a API nativa BarcodeDetector (Chrome/Edge no Android).
   Não há entrada manual nem captura de coletor: esta tela é só câmera.
   ========================================================================== */

(function () {
    "use strict";

    var MATRICULA_STORAGE_KEY = "connectmx-romaneio-default-user";
    var SCAN_FORMATS = [
        "code_128", "code_39", "code_93", "codabar", "itf",
        "ean_13", "ean_8", "upc_a", "upc_e",
        "qr_code", "data_matrix", "pdf417", "aztec"
    ];

    // Etapas da contagem, gravadas em USU_TIPREG. Espelham
    // `SimulationRomaneioEntry.RECORD_TYPE_CHOICES` no servidor.
    var STAGE_LABELS = { 1: "Separar", 2: "Guardar", 3: "Paletizar", 4: "Carregar" };

    var shell = document.querySelector("[data-rmb-shell]");
    if (!shell) return;

    var quickSubmitUrl = shell.dataset.quickSubmitUrl || "";

    var els = {
        matricula: document.getElementById("rmbMatricula"),
        matriculaNext: document.getElementById("rmbMatriculaNext"),
        matriculaEcho: document.getElementById("rmbMatriculaEcho"),
        stageButtons: Array.prototype.slice.call(document.querySelectorAll("[data-rmb-stage]")),
        cameraAlert: document.getElementById("rmbCameraAlert"),
        scanner: document.getElementById("rmbScanner"),
        video: document.getElementById("rmbVideo"),
        scannerStatus: document.getElementById("rmbScannerStatus"),
        readout: document.getElementById("rmbReadout"),
        saveButton: document.getElementById("rmbSaveButton"),
        toast: document.getElementById("rmbToast")
    };

    var stream = null;
    var detector = null;
    var scanLoopId = null;
    var scanning = false;
    var saving = false;
    var currentScan = null;
    // Vale da escolha do botão até a leitura ser salva ou cancelada.
    var currentStage = null;
    var toastTimer = null;

    /* ------------------------------------------------------------ helpers -- */

    function getCsrfToken() {
        var field = document.querySelector("[name=csrfmiddlewaretoken]");
        return field ? field.value : "";
    }

    function getMatricula() {
        return String((els.matricula && els.matricula.value) || "").trim();
    }

    function showToast(message, isError) {
        if (!els.toast) return;
        els.toast.textContent = message;
        els.toast.classList.toggle("is-error", Boolean(isError));
        els.toast.classList.add("is-visible");
        window.clearTimeout(toastTimer);
        toastTimer = window.setTimeout(function () {
            els.toast.classList.remove("is-visible");
        }, 3600);
    }

    function setScannerStatus(message) {
        if (els.scannerStatus) els.scannerStatus.textContent = message;
    }

    function setStep(step) {
        shell.dataset.step = step;
        window.scrollTo({ top: 0, behavior: "auto" });
    }

    function vibrate(pattern) {
        if (navigator.vibrate) {
            try { navigator.vibrate(pattern); } catch (error) { /* sem retorno tátil */ }
        }
    }

    /* -------------------------------------------------- leitura do payload -- */

    // Mesmas regras do servidor (_split_romaneio_payload / _map_romaneio_payload):
    // o código traz 6 campos separados por quebra de linha, tab, / , | ou ; —
    // Empresa/Filial/Volumes/Peso/Código do pallet/Endereçamento.
    function splitPayload(payload) {
        var source = String(payload || "").trim();
        if (!source) return [];
        var splitters = [/\r?\n/, /\t/, /\//, /\|/, /;/];
        for (var i = 0; i < splitters.length; i += 1) {
            var parts = source.split(splitters[i]).map(function (item) {
                return item.trim();
            }).filter(Boolean);
            if (parts.length === 6) return parts;
        }
        return [];
    }

    function mapPayload(payload) {
        var parts = splitPayload(payload);
        if (parts.length !== 6) return null;
        return {
            company: parts[0],
            branch: parts[1],
            volumes: parts[2],
            weight: parts[3],
            packageCode: parts[4],
            addressCode: parts[5]
        };
    }

    function buildTile(label, value, wide) {
        var tile = document.createElement("div");
        tile.className = "rmb-tile" + (wide ? " is-wide" : "");
        var caption = document.createElement("span");
        caption.textContent = label;
        var content = document.createElement("strong");
        content.textContent = value || "—";
        tile.appendChild(caption);
        tile.appendChild(content);
        return tile;
    }

    function renderReadout(scan) {
        if (!els.readout) return;
        els.readout.innerHTML = "";

        // A contagem de volumes é o que a pessoa confere de relance: fica sozinha
        // no bloco de destaque. Empresa, filial, pallet, endereço e peso só
        // confirmam que a leitura é do romaneio certo.
        var hero = document.createElement("div");
        hero.className = "rmb-hero";
        var heroValue = document.createElement("span");
        heroValue.className = "rmb-hero-value";
        heroValue.textContent = scan.volumes || "—";
        var heroLabel = document.createElement("span");
        heroLabel.className = "rmb-hero-label";
        heroLabel.textContent = "Volumes";
        hero.appendChild(heroValue);
        hero.appendChild(heroLabel);
        els.readout.appendChild(hero);

        var grid = document.createElement("div");
        grid.className = "rmb-grid";
        // A etapa vem primeiro por ser o único dado que não saiu da etiqueta:
        // é o que a pessoa ainda pode ter escolhido errado.
        grid.appendChild(buildTile("Etapa", STAGE_LABELS[currentStage] || "—"));
        grid.appendChild(buildTile("Empresa", scan.company));
        grid.appendChild(buildTile("Filial", scan.branch));
        grid.appendChild(buildTile("Código do pallet", scan.packageCode, true));
        grid.appendChild(buildTile("Endereçamento", scan.addressCode));
        grid.appendChild(buildTile("Peso", scan.weight));
        els.readout.appendChild(grid);
    }

    function acceptPayload(payload) {
        var mapped = mapPayload(payload);
        if (!mapped) {
            setScannerStatus("Não deu para separar os campos dessa leitura. Aponte para o código do romaneio.");
            vibrate([90, 60, 90]);
            scanning = true;
            scanLoop();
            return;
        }

        currentScan = {
            payload: String(payload).trim(),
            company: mapped.company,
            branch: mapped.branch,
            packageCode: mapped.packageCode,
            addressCode: mapped.addressCode,
            volumes: mapped.volumes,
            weight: mapped.weight
        };
        vibrate(70);
        closeScanner();
        renderReadout(currentScan);
        setStep("conferencia");
    }

    /* -------------------------------------------------------------- câmera -- */

    function supportsCamera() {
        return Boolean(
            window.isSecureContext &&
            navigator.mediaDevices &&
            navigator.mediaDevices.getUserMedia &&
            window.BarcodeDetector
        );
    }

    async function buildDetector() {
        if (detector) return detector;
        var available = SCAN_FORMATS;
        try {
            var supported = await window.BarcodeDetector.getSupportedFormats();
            available = SCAN_FORMATS.filter(function (format) {
                return supported.indexOf(format) !== -1;
            });
        } catch (error) {
            available = SCAN_FORMATS;
        }
        detector = available.length
            ? new window.BarcodeDetector({ formats: available })
            : new window.BarcodeDetector();
        return detector;
    }

    async function startCamera() {
        setScannerStatus("Abrindo a câmera...");
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    facingMode: { ideal: "environment" },
                    width: { ideal: 1280 },
                    height: { ideal: 720 }
                },
                audio: false
            });
        } catch (error) {
            // Fechar em vez de deixar a tela preta: o motivo fica no passo 2.
            closeScanner();
            setStep("ler");
            if (els.cameraAlert) {
                els.cameraAlert.hidden = false;
                els.cameraAlert.textContent = cameraErrorMessage(error);
            }
            showToast("Não foi possível abrir a câmera.", true);
            return;
        }

        els.video.srcObject = stream;
        els.video.setAttribute("playsinline", "true");
        try { await els.video.play(); } catch (error) { /* o autoplay já cobre o caso normal */ }

        await buildDetector();
        setScannerStatus("Enquadre o código de barras do pallet.");
        scanning = true;
        scanLoop();
    }

    function stopCamera() {
        scanning = false;
        if (scanLoopId) {
            window.clearTimeout(scanLoopId);
            scanLoopId = null;
        }
        if (stream) {
            stream.getTracks().forEach(function (track) { track.stop(); });
            stream = null;
        }
        if (els.video) els.video.srcObject = null;
    }

    async function scanLoop() {
        if (!scanning || !detector) return;
        try {
            if (els.video.readyState >= 2) {
                var codes = await detector.detect(els.video);
                if (codes && codes.length) {
                    var value = String(codes[0].rawValue || "").trim();
                    if (value) {
                        scanning = false;
                        acceptPayload(value);
                        return;
                    }
                }
            }
        } catch (error) {
            // Frames intermediários falham com frequência; seguimos tentando.
        }
        scanLoopId = window.setTimeout(scanLoop, 130);
    }

    /* ----------------------------------------------------- tela de leitura -- */

    function openScanner() {
        // Sem câmera disponível a tela de leitura seria só um retângulo preto:
        // melhor não abrir e deixar o motivo à vista no passo anterior.
        if (!supportsCamera()) {
            showCameraAlert();
            showToast("Câmera indisponível neste acesso.", true);
            return;
        }
        currentScan = null;
        els.scanner.classList.add("is-open");
        document.body.style.overflow = "hidden";
        startCamera();
    }

    function closeScanner() {
        stopCamera();
        els.scanner.classList.remove("is-open");
        document.body.style.overflow = "";
    }

    function cancelScanner() {
        closeScanner();
        setStep("ler");
    }

    function cameraUnavailableMessage() {
        if (!window.isSecureContext) {
            return "O navegador bloqueia a câmera em páginas http://. Para ler o pallet, "
                + "abra o ConnectMX por https:// — o endereço atual é " + window.location.origin + ".";
        }
        return "Este navegador não decodifica código de barras. Use o Chrome ou o Edge no Android.";
    }

    function cameraErrorMessage(error) {
        var name = (error && error.name) || "";
        if (name === "NotAllowedError" || name === "SecurityError") {
            return "A permissão de câmera foi negada para este site. Libere em Configurações do "
                + "navegador → Configurações do site → Câmera e tente de novo.";
        }
        if (name === "NotFoundError" || name === "OverconstrainedError") {
            return "Nenhuma câmera traseira foi encontrada neste aparelho.";
        }
        if (name === "NotReadableError") {
            return "A câmera está ocupada por outro aplicativo. Feche-o e tente de novo.";
        }
        return "Não foi possível abrir a câmera" + (name ? " (" + name + ")" : "") + ".";
    }

    function showCameraAlert() {
        if (!els.cameraAlert) return;
        els.cameraAlert.hidden = false;
        els.cameraAlert.textContent = cameraUnavailableMessage();
    }

    /* ---------------------------------------------------------------- save -- */

    async function saveScan() {
        if (saving || !currentScan) return;
        if (!currentStage) {
            showToast("Escolha a etapa da contagem antes de salvar.", true);
            setStep("ler");
            return;
        }
        var matricula = getMatricula();
        if (!matricula) {
            showToast("Informe a matrícula antes de salvar.", true);
            setStep("matricula");
            return;
        }
        if (!quickSubmitUrl) {
            showToast("A rota de gravação não foi encontrada na página.", true);
            return;
        }

        saving = true;
        els.saveButton.disabled = true;
        els.saveButton.textContent = "Salvando leitura...";

        try {
            var response = await fetch(quickSubmitUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCsrfToken()
                },
                body: JSON.stringify({
                    barcode_payload: currentScan.payload,
                    user_code: matricula,
                    record_type: currentStage
                })
            });
            var data = await response.json();

            if (response.ok && data.status === "ok") {
                showToast(data.message || "Leitura salva.");
                vibrate(50);
            } else {
                showToast(data.message || "Falha ao salvar a leitura.", true);
                vibrate([90, 60, 90]);
            }
        } catch (error) {
            showToast("Falha de comunicação com o servidor.", true);
        } finally {
            saving = false;
            els.saveButton.disabled = false;
            els.saveButton.textContent = "Salvar Leitura";
            currentScan = null;
            setStep("ler");
        }
    }

    /* ------------------------------------------------------------ matrícula -- */

    function persistMatricula() {
        try {
            var value = getMatricula();
            if (value) {
                window.localStorage.setItem(MATRICULA_STORAGE_KEY, value);
            } else {
                window.localStorage.removeItem(MATRICULA_STORAGE_KEY);
            }
        } catch (error) { /* navegador sem storage: segue sem lembrar */ }
    }

    function restoreMatricula() {
        if (!els.matricula) return;
        try {
            var stored = window.localStorage.getItem(MATRICULA_STORAGE_KEY) || "";
            if (stored) els.matricula.value = stored;
        } catch (error) { /* idem */ }
        syncMatriculaButton();
    }

    function syncMatriculaButton() {
        if (els.matriculaNext) els.matriculaNext.disabled = !getMatricula();
    }

    function confirmMatricula() {
        if (!getMatricula()) return;
        persistMatricula();
        if (els.matriculaEcho) els.matriculaEcho.textContent = getMatricula();
        setStep("ler");
    }

    /* ---------------------------------------------------------------- init -- */

    if (els.matricula) {
        els.matricula.addEventListener("input", syncMatriculaButton);
        els.matricula.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                confirmMatricula();
            }
        });
    }

    if (els.matriculaNext) els.matriculaNext.addEventListener("click", confirmMatricula);
    els.stageButtons.forEach(function (button) {
        button.addEventListener("click", function () {
            var stage = parseInt(button.dataset.rmbStage, 10);
            if (!STAGE_LABELS[stage]) return;
            currentStage = stage;
            openScanner();
        });
    });
    if (els.saveButton) els.saveButton.addEventListener("click", saveScan);

    document.querySelectorAll("[data-rmb-back-matricula]").forEach(function (node) {
        node.addEventListener("click", function () {
            setStep("matricula");
        });
    });

    document.querySelectorAll("[data-rmb-cancel-scan]").forEach(function (node) {
        node.addEventListener("click", cancelScanner);
    });

    document.querySelectorAll("[data-rmb-cancel-review]").forEach(function (node) {
        node.addEventListener("click", function () {
            currentScan = null;
            setStep("ler");
        });
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && els.scanner.classList.contains("is-open")) {
            cancelScanner();
        }
    });

    document.addEventListener("visibilitychange", function () {
        if (document.hidden && scanning) stopCamera();
    });

    // Avisar antes do clique: o botão "Ler Pallet" não tem como funcionar aqui.
    if (!supportsCamera()) showCameraAlert();

    restoreMatricula();
    setStep("matricula");
}());
