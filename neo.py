import sounddevice as sd
import soundfile as sf
import requests
import os
import numpy as np
import time
import pyttsx3
import threading
import re
from collections import deque
from dotenv import load_dotenv

# Inicialização de COM 
try:
    import pythoncom
    TEM_PYTHONCOM = True
except ImportError:
    TEM_PYTHONCOM = False

load_dotenv()

# ─── CONFIGURAÇÕES ────────────────────────────────────────────
# API KEYS 
WHISPER_API_KEY = os.getenv("OPENAI_API_KEY", "")
CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


USAR_WHISPER_API = bool(WHISPER_API_KEY)
USAR_CLAUDE_API = bool(CLAUDE_API_KEY)

# Fallback local (API)
MODELO_WHISPER_LOCAL = "tiny"
MODELO_OLLAMA_LOCAL = "qwen2.5:7b"

PALAVRA_CHAVE = "Neo"
TAXA = 16000
DURACAO_MAX = 5
SILÊNCIO_MIN = 0.8
THRESHOLD_SILÊNCIO = 0.02


JANELA_PALAVRA_CHAVE = 3.5

INTERVALO_CHECAGEM_PALAVRA = 2.0

TAMANHO_BLOCO = 1024

# ─── STATUS VISUAL ────────────────────────────────────────────
def mostrar_processando(mensagem="Processando"):
    """Mostra animação enquanto processa"""
    print(f"   ⏳ {mensagem}", end="", flush=True)
    for _ in range(3):
        time.sleep(0.15)
        print(".", end="", flush=True)
    print()

# ─── VOZ ──────────────────────────────────────────────────────
VOICE_ID = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_EN-US_DAVID_11.0"

def falar(texto, bloqueante=False):
    """Fala o texto - não bloqueante por padrão"""
    print(f"🤖 Neo: {texto}")

    def _falar_com_com():
        com_iniciado = False
        if TEM_PYTHONCOM:
            try:
                pythoncom.CoInitialize()
                com_iniciado = True
            except Exception:
                pass
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 200)
            engine.setProperty("volume", 1.0)
            if VOICE_ID:
                try:
                    engine.setProperty("voice", VOICE_ID)
                except Exception:
                    pass
            engine.say(texto)
            engine.runAndWait()
        except Exception as e:
            print(f"   [Erro fala: {e}]")
        finally:
            if com_iniciado:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    if bloqueante:
        _falar_com_com()
    else:
        threading.Thread(target=_falar_com_com, daemon=True).start()

# ─── CALIBRAÇÃO ───────────────────────────────────────────────
def calibrar_microfone():
    print("🎙️  Calibrando... silêncio por 2s.")
    amostras = []
    for _ in range(2):
        audio = sd.rec(int(1 * TAXA), samplerate=TAXA, channels=1, dtype="float32")
        sd.wait()
        amostras.append(np.abs(audio).max())
    ruido_max = max(amostras)
    threshold = max(ruido_max * 6, 0.05)
    print(f"✅ Ruído: {ruido_max:.5f} | Threshold: {threshold:.5f}")
    return threshold

# ─── TRANSCRIÇÃO (WHISPER API + Local fallback) ───────────────
modelo_whisper_local = None
_lock_whisper_local = threading.Lock()

def carregar_whisper_local():
    """Carrega modelo local apenas se precisar (thread-safe)"""
    global modelo_whisper_local
    with _lock_whisper_local:
        if modelo_whisper_local is None:
            import whisper
            print("⏳ Carregando Whisper local (fallback)...")
            modelo_whisper_local = whisper.load_model(MODELO_WHISPER_LOCAL)
            print("✅ Whisper local pronto!")
    return modelo_whisper_local

def transcrever_whisper_api(audio_path):
    """Transcreve usando Whisper API da OpenAI (muito mais preciso)"""
    try:
        with open(audio_path, "rb") as f:
            audio_data = f.read()

        headers = {"Authorization": f"Bearer {WHISPER_API_KEY}"}
        files = {"file": ("comando.wav", audio_data, "audio/wav")}
        data = {"model": "whisper-1", "language": "pt"}

        print("   [📡 Enviando para Whisper API...]")
        r = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers=headers, files=files, data=data, timeout=30
        )
        r.raise_for_status()
        resultado = r.json()
        texto = resultado["text"].strip()
        print(f"🗣️  Whisper API: '{texto}'")
        return texto
    except Exception as e:
        print(f"   [⚠️  Whisper API falhou: {e}]")
        return None

def transcrever_whisper_local(audio_path):
    """Transcreve usando Whisper local (fallback)"""
    try:
        modelo = carregar_whisper_local()
        print("   [🎯 Transcrevendo local...]")
        resultado = modelo.transcribe(audio_path, language="pt")
        texto = resultado["text"].strip()
        print(f"🗣️  Whisper local: '{texto}'")
        return texto
    except Exception as e:
        print(f"   [⚠️  Whisper local falhou: {e}]")
        return ""

def transcrever_audio_array(audio_array):
    """Salva um array de áudio em arquivo temporário e transcreve (API ou local)"""
    caminho = "comando.wav"
    sf.write(caminho, audio_array, TAXA)

    if USAR_WHISPER_API:
        texto = transcrever_whisper_api(caminho)
        if texto is not None:
            return texto

    return transcrever_whisper_local(caminho)

def gravar_com_detecção_de_silêncio():
    """Grava áudio (stream próprio) e para quando detecta silêncio"""
    print("🎤 Fale...")
    audio_buffer = deque()
    max_samples = int(DURACAO_MAX * TAXA)
    silêncio_contínuo = 0
    min_silêncio_samples = int(SILÊNCIO_MIN * TAXA)
    começou_a_falar = False

    stream = sd.InputStream(samplerate=TAXA, channels=1, dtype="float32")
    stream.start()
    try:
        print("   [ouvindo...]")
        while len(audio_buffer) < max_samples:
            audio, _ = stream.read(512)
            audio_buffer.extend(audio.flatten())

            rms = np.sqrt(np.mean(audio ** 2))

            if rms > THRESHOLD_SILÊNCIO:
                começou_a_falar = True
                silêncio_contínuo = 0
            elif começou_a_falar:
                silêncio_contínuo += len(audio)
                if silêncio_contínuo >= min_silêncio_samples:
                    print("   [silêncio detectado]")
                    break
    finally:
        stream.stop()
        stream.close()

    audio_array = np.array(audio_buffer, dtype="float32")
    print("   [transcrevendo...]")
    return transcrever_audio_array(audio_array)

# ─── PALAVRA-CHAVE ────────────────────────────────────────────
def contem_palavra_chave(texto):
    texto = texto.lower().strip()
    if not texto:
        return False
    print(f"   [verificando: '{texto}']")
    if "neo" in texto:
        print("   [MATCH: 'neo' encontrado!]")
        return True
    variacoes = ["nio", "néu", "neu", "niu", "nyo"]
    for v in variacoes:
        if v in texto:
            print(f"   [MATCH: '{v}' encontrado!]")
            return True
    return False

# ─── DETECÇÃO DE GATILHO ──
def aguardar_gatilho(threshold_palmas):
    """
    Escuta continuamente em UM ÚNICO stream de áudio e verifica, no mesmo
    laço, tanto palmas (pico de amplitude) quanto a palavra-chave (via
    transcrição periódica de uma janela rolante). Evita abrir dois streams
    de entrada simultâneos, que podem conflitar no mesmo dispositivo.
    Retorna "palmas" ou "palavra_chave".
    """
    janela_amostras = int(JANELA_PALAVRA_CHAVE * TAXA)
    buffer_rolante = deque(maxlen=janela_amostras)

    resultado = {"valor": None}
    lock = threading.Lock()

    def checar_palavra_async(audio_copia):
        try:
            texto = transcrever_whisper_local_bruto(audio_copia)
            print(f"   [escuta PT: '{texto}']")
            if contem_palavra_chave(texto):
                with lock:
                    if not resultado["valor"]:
                        resultado["valor"] = "palavra_chave"
        except Exception as e:
            print(f"   [Erro checar_palavra: {e}]")

    ultima_checagem = 0.0
    checagem_em_andamento = False

    stream = sd.InputStream(samplerate=TAXA, channels=1, dtype="float32")
    stream.start()
    try:
        while True:
            with lock:
                if resultado["valor"]:
                    return resultado["valor"]

            audio, _ = stream.read(TAMANHO_BLOCO)
            chunk = audio.flatten()
            buffer_rolante.extend(chunk)

            pico = np.abs(chunk).max()
            media = np.abs(chunk).mean() + 1e-9
            if pico > threshold_palmas and pico > (media * 5):
                return "palmas"

            agora = time.time()
            if (
                not checagem_em_andamento
                and len(buffer_rolante) >= janela_amostras
                and (agora - ultima_checagem) >= INTERVALO_CHECAGEM_PALAVRA
            ):
                ultima_checagem = agora
                checagem_em_andamento = True
                audio_copia = np.array(buffer_rolante, dtype="float32")

                def _tarefa(copia=audio_copia):
                    nonlocal checagem_em_andamento
                    checar_palavra_async(copia)
                    checagem_em_andamento = False

                threading.Thread(target=_tarefa, daemon=True).start()
    finally:
        stream.stop()
        stream.close()

def transcrever_whisper_local_bruto(audio_array):
    """Transcreve um array de áudio usando o Whisper local (usado na detecção de gatilho)"""
    modelo = carregar_whisper_local()
    caminho = "escuta_kw.wav"
    sf.write(caminho, audio_array, TAXA)
    resultado = modelo.transcribe(caminho, language="pt")
    return resultado["text"].strip()

# ─── LLM (CLAUDE API + Ollama fallback) ───────────────────────
CLAUDE_SYSTEM_PROMPT = """Você é Neo, assistente Windows em PT-BR.

FORMATOS DE AÇÃO (use exatamente assim):
- ABRIR:programa
- FECHAR:programa
- CMD:comando_terminal
- DÚVIDA:pergunta (quando não entender)

REGRAS:
1. Comando claro → use formato de ação
2. Comando ambíguo → use DÚVIDA:
3. Seja breve (1 frase)

Exemplos:
"abre chrome" → ABRIR:chrome
"fecha calculadora" → FECHAR:calculadora
"blabla xyz" → DÚVIDA:Não entendi. Você quis dizer abrir algum programa?"""

def perguntar_claude(comando):
    """Pergunta para o Claude API (mais inteligente)"""
    try:
        headers = {
            "x-api-key": CLAUDE_API_KEY,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01"
        }


        data = {
            "model": "claude-3-haiku-20240307",
            "max_tokens": 100,
            "system": CLAUDE_SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": comando}
            ]
        }

        print("   [📡 Enviando para Claude API...]")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers, json=data, timeout=15
        )
        r.raise_for_status()
        resultado = r.json()
        resposta = resultado["content"][0]["text"].strip()
        print(f"🤖 Claude: '{resposta}'")
        return resposta

    except Exception as e:
        print(f"   [⚠️  Claude API falhou: {e}]")
        return None

def perguntar_ollama(comando):
    """Fallback para Ollama local"""
    try:
        print("   [🏠 Consultando Ollama local...]")
        r = requests.post("http://localhost:11434/api/generate", json={
            "model": MODELO_OLLAMA_LOCAL,
            "prompt": f"""Neo - Windows assistant PT-BR.
Formatos: ABRIR:x, FECHAR:x, CMD:x, DÚVIDA:x
User: "{comando}"
Neo:""",
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 50}
        }, timeout=20)
        r.raise_for_status()
        response_json = r.json()
        resposta = response_json.get("response", "").strip()
        print(f"🤖 Ollama: '{resposta}'")
        return resposta or None
    except Exception as e:
        print(f"   [⚠️  Ollama falhou: {e}]")
        return None

def perguntar_ia(comando):
    """Pergunta para IA: tenta Claude API, fallback Ollama"""
    mostrar_processando("Pensando")

    if USAR_CLAUDE_API:
        resposta = perguntar_claude(comando)
        if resposta:
            return resposta

    return perguntar_ollama(comando) or "Não entendi o comando."

# ─── EXECUÇÃO DE COMANDOS ─────────────────────────────────────
PROGRAMAS = {
    "calculadora": {"abre": "calc", "processo": "calculator"},
    "calc": {"abre": "calc", "processo": "calculator"},
    "bloco de notas": {"abre": "notepad", "processo": "notepad"},
    "bloco notas": {"abre": "notepad", "processo": "notepad"},
    "notepad": {"abre": "notepad", "processo": "notepad"},
    "bloco": {"abre": "notepad", "processo": "notepad"},
    "vs code": {"abre": "code", "processo": "code"},
    "code": {"abre": "code", "processo": "code"},
    "visual studio code": {"abre": "code", "processo": "code"},
    "vscode": {"abre": "code", "processo": "code"},
    "chrome": {"abre": "chrome", "processo": "chrome"},
    "google chrome": {"abre": "chrome", "processo": "chrome"},
    "edge": {"abre": "msedge", "processo": "msedge"},
    "microsoft edge": {"abre": "msedge", "processo": "msedge"},
    "explorer": {"abre": "explorer", "processo": "explorer"},
    "explorador": {"abre": "explorer", "processo": "explorer"},
    "arquivos": {"abre": "explorer", "processo": "explorer"},
    "terminal": {"abre": "wt", "processo": "wt"},
    "powershell": {"abre": "powershell", "processo": "powershell"},
    "cmd": {"abre": "cmd", "processo": "cmd"},
    "spotify": {"abre": "spotify", "processo": "spotify"},
    "discord": {"abre": "discord", "processo": "discord"},
    "calculator": {"abre": "calc", "processo": "calculator"},
    "browser": {"abre": "chrome", "processo": "chrome"},
}

def abrir_programa(nome):
    """Abre um programa pelo nome amigável"""
    nome = nome.lower().strip()

    if nome in PROGRAMAS:
        cmd = PROGRAMAS[nome]["abre"]
        print(f"▶️  Abrindo: {cmd}")
        os.system(f'start {cmd}')
        return True, f"Abrindo {nome}!"

    try:
        os.system(f'start {nome}')
        return True, f"Abrindo {nome}!"
    except Exception:
        return False, f"Não achei '{nome}'."

def fechar_programa(nome):
    """Fecha um programa pelo nome amigável"""
    nome = nome.lower().strip()

    if nome in PROGRAMAS:
        processo = PROGRAMAS[nome].get("processo", nome)
    else:
        processo = nome

    print(f"   [🔪 Tentando fechar: {processo}.exe]")

    try:
        os.system(f'taskkill /im {processo}.exe /f')
        return True, f"Fechando {nome}!"
    except Exception as e:
        return False, f"Erro: {e}"

def processar_comando_local(texto):
    """
    Processa comandos SEM IA - apenas regex local (rápido)
    Retorna: (tem_comando, acao, parametro)
    """
    texto_lower = texto.lower().strip()

    verbos_abrir = ["abre", "abra", "abrir", "abre o", "abre a", "abra o", "abra a",
                    "liga", "ligar", "liga o", "liga a", "inicia", "iniciar",
                    "start", "open", "launch", "run", "execute"]

    verbos_fechar = ["fecha", "fechar", "fecha o", "fecha a", "fecha os", "fecha as",
                     "close", "close the", "kill", "mata", "matar", "encerra", "encerrar"]

    for verbo in sorted(verbos_abrir, key=len, reverse=True):
        if texto_lower.startswith(verbo):
            resto = texto_lower[len(verbo):].strip()
            resto = re.sub(r'^\s*(o|a|os|as|um|uma|the)\s+', '', resto)

            for nome in PROGRAMAS.keys():
                if nome in resto or resto in nome:
                    print(f"   [✓] Local: abrir {nome}")
                    return True, "abrir", nome

            if resto and len(resto) > 2:
                print(f"   [✓] Local: abrir {resto} (direto)")
                return True, "abrir", resto

    for verbo in sorted(verbos_fechar, key=len, reverse=True):
        if texto_lower.startswith(verbo):
            resto = texto_lower[len(verbo):].strip()
            resto = re.sub(r'^\s*(o|a|os|as|the)\s+', '', resto)

            for nome in PROGRAMAS.keys():
                if nome in resto or resto in nome:
                    print(f"   [✓] Local: fechar {nome}")
                    return True, "fechar", nome

            if resto and len(resto) > 2:
                print(f"   [✓] Local: fechar {resto} (direto)")
                return True, "fechar", resto

    return False, None, None

def executar(resposta, texto_original=None):
    """
    Executa comandos com prioridade:
    1. Comandos locais (regex) - mais rápido
    2. Comandos da IA (ABRIR:/FECHAR:/DÚVIDA:)
    3. Resposta normal
    """
    print(f"   [IA: '{resposta}']")

    if texto_original:
        tem_comando, acao, param = processar_comando_local(texto_original)
        if tem_comando:
            if acao == "abrir":
                _, msg = abrir_programa(param)
                falar(msg)
            elif acao == "fechar":
                _, msg = fechar_programa(param)
                falar(msg)
            return

    primeira_linha = resposta.strip().split("\n")[0].strip()

    match = re.search(r'FECHAR:(\S+)', primeira_linha, re.IGNORECASE)
    if match:
        _, msg = fechar_programa(match.group(1).strip())
        falar(msg)
        return

    match = re.search(r'ABRIR:(\S+)', primeira_linha, re.IGNORECASE)
    if match:
        _, msg = abrir_programa(match.group(1).strip())
        falar(msg)
        return

    if ("DÚVIDA:" in resposta.upper() or
        "não entendi" in resposta.lower() or
        ("?" in resposta and len(resposta) < 120)):
        pergunta = resposta.replace("DÚVIDA:", "").strip()
        falar(pergunta)
        return

    falar(resposta)

# ─── MAIN ─────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("           🤖 NEO — ASSISTENTE HÍBRIDO (LOCAL + CLOUD)")
    print("=" * 60)

    whisper_status = "📡 API (OpenAI)" if USAR_WHISPER_API else "🏠 Local (menos preciso)"
    llm_status = "📡 Claude" if USAR_CLAUDE_API else "🏠 Ollama"

    if not USAR_WHISPER_API:
        print("⚠️  OPENAI_API_KEY não configurada - usando Whisper local")
    if not USAR_CLAUDE_API:
        print("⚠️  ANTHROPIC_API_KEY não configurada - usando Ollama")

    print(f"\n🎙️  Transcrição: {whisper_status}")
    print(f"🧠  Inteligência: {llm_status}")
    print(f"🔊 Voz: David")
    print()
    print("📝 Para configurar as APIs, edite: .env")
    print()

    #  Whisper local
    carregar_whisper_local()

    threshold = calibrar_microfone()

    falar("Neo online.")
    print(f"\n👂 Aguardando... (diga '{PALAVRA_CHAVE}' ou palmas)\n")

    while True:
        try:
            trigger_tipo = aguardar_gatilho(threshold)
            trigger = "palmas" if trigger_tipo == "palmas" else "voz"
            print(f"✅ Trigger: {trigger}")
            falar("Yes?")

            texto = gravar_com_detecção_de_silêncio()

            if texto and len(texto) > 2:
                tem_comando, acao, param = processar_comando_local(texto)
                if tem_comando:
                    if acao == "abrir":
                        _, msg = abrir_programa(param)
                        falar(msg)
                    elif acao == "fechar":
                        _, msg = fechar_programa(param)
                        falar(msg)
                else:
                    resposta = perguntar_ia(texto)
                    executar(resposta, texto_original=texto)
            else:
                print("   [ignorado - muito curto]")

            print(f"\n👂 Aguardando...\n")

        except KeyboardInterrupt:
            print("\n⛔ Neo encerrado.")
            break
        except Exception as e:
            print(f"⚠️  Erro: {e}")
            time.sleep(0.5)

if __name__ == "__main__":
    main()