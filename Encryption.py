#Encryption.py
import string
import random

# Expanded alphabet: includes uppercase, lowercase, digits, punctuation, and space
ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits + string.punctuation + " "
ALPHABET_LEN = len(ALPHABET)

# -------------------------------------------------------------
# Caesar cipher (with expanded alphabet)
# -------------------------------------------------------------
def caesar_encrypt(text: str, key: int) -> str:
    result = []
    for char in text:
        if char in ALPHABET:
            idx = (ALPHABET.index(char) + key) % ALPHABET_LEN
            result.append(ALPHABET[idx])
        else:
            result.append(char)
    return "".join(result)

def caesar_decrypt(text: str, key: int) -> str:
    result = []
    for char in text:
        if char in ALPHABET:
            idx = (ALPHABET.index(char) - key) % ALPHABET_LEN
            result.append(ALPHABET[idx])
        else:
            result.append(char)
    return "".join(result)

# -------------------------------------------------------------
# Vigenère cipher (with expanded alphabet)
# -------------------------------------------------------------
def vigenere_encrypt(text: str, key: str) -> str:
    result = []
    key_index = 0
    for char in text:
        if char in ALPHABET:
            shift = ALPHABET.index(key[key_index % len(key)])
            new_idx = (ALPHABET.index(char) + shift) % ALPHABET_LEN
            result.append(ALPHABET[new_idx])
            key_index += 1
        else:
            result.append(char)
    return "".join(result)

def vigenere_decrypt(text: str, key: str) -> str:
    result = []
    key_index = 0
    for char in text:
        if char in ALPHABET:
            shift = ALPHABET.index(key[key_index % len(key)])
            new_idx = (ALPHABET.index(char) - shift) % ALPHABET_LEN
            result.append(ALPHABET[new_idx])
            key_index += 1
        else:
            result.append(char)
    return "".join(result)

# -------------------------------------------------------------
# Number → Letters (same idea, but safe for any number)
# -------------------------------------------------------------
def number_to_letters(num: int) -> str:
    mapping = {i: string.ascii_uppercase[i] for i in range(10)}
    return "".join(mapping[int(d)] for d in str(num))

# -------------------------------------------------------------
# Random noise injection (same as before)
# -------------------------------------------------------------
def insert_random_every2(cipher: str, seed: int = None) -> str:
    rnd = random.Random(seed)
    chars = ALPHABET
    out = []
    count = 0
    for ch in cipher:
        out.append(ch)
        count += 1
        if count == 2:  # insert random noise after every 2 chars
            out.append(rnd.choice(chars))
            count = 0
    return "".join(out)

def remove_random_every2(cipher_with_noise: str) -> str:
    out = []
    count = 0
    for ch in cipher_with_noise:
        count += 1
        if count == 3:  # remove every 3rd character (noise)
            count = 0
            continue
        out.append(ch)
    return "".join(out)

# -------------------------------------------------------------
# Custom Encryption + Decryption Pipeline
# -------------------------------------------------------------
def custom_encrypt(message: str, key_number: int) -> str:
    # Step 0: reverse
    reversed_msg = message[::-1]

    # Step 1: Caesar
    after_caesar = caesar_encrypt(reversed_msg, key_number)

    # Step 2: derive Vigenère key from numeric key
    after_caesar_key = number_to_letters(key_number)
    vig_key_final = caesar_encrypt(after_caesar_key, key_number)

    # Step 3: Vigenère
    final_cipher = vigenere_encrypt(after_caesar, vig_key_final)

    # Step 4: Add random noise (optional but fun)
    final_cipher = insert_random_every2(final_cipher, seed=key_number)

    return final_cipher


def custom_decrypt(ciphertext: str, key_number: int) -> str:
    # Step 0: remove noise
    cleaned = remove_random_every2(ciphertext)

    # Step 1: derive same Vigenère key
    after_caesar_key = number_to_letters(key_number)
    vig_key_final = caesar_encrypt(after_caesar_key, key_number)

    # Step 2: undo Vigenère
    after_vigenere = vigenere_decrypt(cleaned, vig_key_final)

    # Step 3: undo Caesar
    after_caesar = caesar_decrypt(after_vigenere, key_number)

    # Step 4: reverse again
    plaintext = after_caesar[::-1]
    return plaintext

# -------------------------------------------------------------
# ✅ Example Test
# -------------------------------------------------------------
if __name__ == "__main__":
    password = "jessica45#4567!Qw"
    key_number = 73

    encrypted = custom_encrypt(password, key_number)
    print("Encrypted:", encrypted)

    decrypted = custom_decrypt(encrypted, key_number)
    print("Decrypted:", decrypted)


