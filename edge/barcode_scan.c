#include <windows.h>
#include <bcrypt.h>
#include <stdio.h>
#include <string.h>
#include <ctype.h>

// Automatically link the CNG library if using the Microsoft compiler (MSVC)
#pragma comment(lib, "bcrypt.lib")

// The secret key for the Vault Gatekeeper. 
// Note: In production, load this from a secure config file or environment variable.
const char* MUSTER_EDIPI_KEY = "super_secret_muster_edipi_key_32_chars+";

// ---------------------------------------------------------
// 1. Parser: Extract the 10-digit DoD ID (EDIPI)
// ---------------------------------------------------------
int ExtractDoDID(const char* rawBarcode, char* outputId) {
    int len = strlen(rawBarcode);
    int consecutiveDigits = 0;
    
    for (int i = 0; i < len; i++) {
        if (isdigit(rawBarcode[i])) {
            consecutiveDigits++;
            if (consecutiveDigits == 10) {
                // We found 10 consecutive digits. Copy them.
                strncpy(outputId, &rawBarcode[i - 9], 10);
                outputId[10] = '\0';
                return 1; // Success
            }
        } else {
            consecutiveDigits = 0; // Reset if we hit a letter or delimiter
        }
    }
    return 0; // Failed to find a 10-digit ID
}

// ---------------------------------------------------------
// 2. Hasher: Compute HMAC-SHA256 using the MUSTER secret
// ---------------------------------------------------------
int ComputeHMACSHA256(const char* inputData, const char* secretKey, char* outputHashHex) {
    BCRYPT_ALG_HANDLE hAlg = NULL;
    BCRYPT_HASH_HANDLE hHash = NULL;
    DWORD cbHashObject = 0;
    DWORD cbData = 0;
    PBYTE pbHashObject = NULL;
    BYTE pbHash[32]; // SHA-256 outputs 32 bytes
    int success = 0;

    // Open an algorithm handle for SHA256 with the HMAC flag
    if (BCryptOpenAlgorithmProvider(&hAlg, BCRYPT_SHA256_ALGORITHM, NULL, BCRYPT_ALG_HANDLE_HMAC_FLAG) >= 0) {
        
        // Determine the size of the hash object memory needed
        if (BCryptGetProperty(hAlg, BCRYPT_OBJECT_LENGTH, (PBYTE)&cbHashObject, sizeof(DWORD), &cbData, 0) >= 0) {
            pbHashObject = (PBYTE)HeapAlloc(GetProcessHeap(), 0, cbHashObject);
            
            if (pbHashObject != NULL) {
                // Initialize the hash with the MUSTER_EDIPI_KEY
                if (BCryptCreateHash(hAlg, &hHash, pbHashObject, cbHashObject, (PUCHAR)secretKey, (ULONG)strlen(secretKey), 0) >= 0) {
                    
                    // Feed the plaintext EDIPI into the HMAC
                    if (BCryptHashData(hHash, (PUCHAR)inputData, (ULONG)strlen(inputData), 0) >= 0) {
                        
                        // Finalize and extract the 32-byte HMAC
                        if (BCryptFinishHash(hHash, pbHash, sizeof(pbHash), 0) >= 0) {
                            
                            // Convert the raw bytes to a readable hex string for the Vault Gatekeeper
                            const char hexChars[] = "0123456789abcdef";
                            for (int i = 0; i < 32; i++) {
                                outputHashHex[i * 2] = hexChars[(pbHash[i] >> 4) & 0x0F];
                                outputHashHex[i * 2 + 1] = hexChars[pbHash[i] & 0x0F];
                            }
                            outputHashHex[64] = '\0'; // Null-terminate the 64-character hex string
                            success = 1;
                        }
                    }
                    BCryptDestroyHash(hHash);
                }
                HeapFree(GetProcessHeap(), 0, pbHashObject);
            }
        }
        BCryptCloseAlgorithmProvider(hAlg, 0);
    }
    return success;
}

// ---------------------------------------------------------
// 3. Main Security Controller (PBI-04 L24)
// ---------------------------------------------------------
void ProcessCACBarcode(char* rawScannerInput) {
    char extractedId[11];      // 10 digits + null terminator
    char hmacHex[65];          // 64 hex chars for HMAC-SHA256 + null terminator

    printf("Processing incoming CAC scan...\n");

    // Step 1: Parse the EDIPI from the PDF417 wedge string
    if (ExtractDoDID(rawScannerInput, extractedId)) {
        
        // Step 2: Hash the ID with the Vault secret key
        if (ComputeHMACSHA256(extractedId, MUSTER_EDIPI_KEY, hmacHex)) {
            
            // hmacHex is now the secure, append-only ready token
            printf("Success! EDIPI HMAC generated: %s\n", hmacHex);
            
            // -> PASS hmacHex TO YOUR UI OR NETWORK FUNCTION HERE <-
            
        } else {
            printf("Error: HMAC computation failed.\n");
        }
    } else {
        printf("Error: Could not locate a valid 10-digit EDIPI in the barcode string.\n");
    }

    // Step 3: SECURE CLEANUP (Append-only / Zero-trust compliance)
    // SecureZeroMemory physically prevents the compiler from optimizing away the wipe,
    // ensuring the plaintext EDIPI and the raw PDF417 string are permanently gone from RAM.
    SecureZeroMemory(rawScannerInput, strlen(rawScannerInput));
    SecureZeroMemory(extractedId, sizeof(extractedId));
}