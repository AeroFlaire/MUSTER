#include <windows.h> //Gemini Pro
#include <bcrypt.h>
#include <string.h>
#include <ctype.h>

#pragma comment(lib, "bcrypt.lib")

// UI Element IDs
#define IDC_INPUT_BARCODE 1001
#define IDC_OUTPUT_CADET  1002
#define IDC_BTN_PROCESS   1003

// Global Window Handles
HWND hInputBox;
HWND hOutputField;
HWND hProcessBtn;

const char* MUSTER_EDIPI_KEY = "super_secret_muster_edipi_key_32_chars+";

// ---------------------------------------------------------
// 1. Core Logic: Parse, Hash, and Wipe
// ---------------------------------------------------------
int ExtractDoDID(const char* rawBarcode, char* outputId) {
    int len = strlen(rawBarcode);
    int consecutiveDigits = 0;
    for (int i = 0; i < len; i++) {
        if (isdigit(rawBarcode[i])) {
            consecutiveDigits++;
            if (consecutiveDigits == 10) {
                strncpy(outputId, &rawBarcode[i - 9], 10);
                outputId[10] = '\0';
                return 1;
            }
        } else {
            consecutiveDigits = 0;
        }
    }
    return 0;
}

int ComputeHMACSHA256(const char* inputData, const char* secretKey, char* outputHashHex) {
    BCRYPT_ALG_HANDLE hAlg = NULL;
    BCRYPT_HASH_HANDLE hHash = NULL;
    DWORD cbHashObject = 0, cbData = 0;
    PBYTE pbHashObject = NULL;
    BYTE pbHash[32];
    int success = 0;

    if (BCryptOpenAlgorithmProvider(&hAlg, BCRYPT_SHA256_ALGORITHM, NULL, BCRYPT_ALG_HANDLE_HMAC_FLAG) >= 0) {
        if (BCryptGetProperty(hAlg, BCRYPT_OBJECT_LENGTH, (PBYTE)&cbHashObject, sizeof(DWORD), &cbData, 0) >= 0) {
            pbHashObject = (PBYTE)HeapAlloc(GetProcessHeap(), 0, cbHashObject);
            if (pbHashObject != NULL) {
                if (BCryptCreateHash(hAlg, &hHash, pbHashObject, cbHashObject, (PUCHAR)secretKey, (ULONG)strlen(secretKey), 0) >= 0) {
                    if (BCryptHashData(hHash, (PUCHAR)inputData, (ULONG)strlen(inputData), 0) >= 0) {
                        if (BCryptFinishHash(hHash, pbHash, sizeof(pbHash), 0) >= 0) {
                            const char hexChars[] = "0123456789abcdef";
                            for (int i = 0; i < 32; i++) {
                                outputHashHex[i * 2] = hexChars[(pbHash[i] >> 4) & 0x0F];
                                outputHashHex[i * 2 + 1] = hexChars[pbHash[i] & 0x0F];
                            }
                            outputHashHex[64] = '\0';
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
// 2. UI Event Handler
// ---------------------------------------------------------
void HandleScanEvent() {
    // Allocate a buffer large enough for a massive PDF417 string
    char rawInput[2048] = {0}; 
    char extractedId[11] = {0};
    char hmacHex[65] = {0};

    // Pull the pasted string from the input box
    GetWindowText(hInputBox, rawInput, sizeof(rawInput));

    // Process it
    if (ExtractDoDID(rawInput, extractedId)) {
        if (ComputeHMACSHA256(extractedId, MUSTER_EDIPI_KEY, hmacHex)) {
            // Fill the "cadet field" with the secure hashed ID
            SetWindowText(hOutputField, hmacHex);
        } else {
            SetWindowText(hOutputField, "Error: HMAC Computation Failed");
        }
    } else {
        SetWindowText(hOutputField, "Error: No 10-digit EDIPI found in string");
    }

    // SECURE WIPE: Destroy the raw string and plaintext ID from RAM
    SecureZeroMemory(rawInput, sizeof(rawInput));
    SecureZeroMemory(extractedId, sizeof(extractedId));
    
    // Clear the input box so the raw string is not left sitting in the UI
    SetWindowText(hInputBox, "");
}

LRESULT CALLBACK WindowProc(HWND hwnd, UINT uMsg, WPARAM wParam, LPARAM lParam) {
    switch (uMsg) {
        case WM_CREATE:
            CreateWindow("STATIC", "1. Paste Raw Barcode Here:", WS_CHILD | WS_VISIBLE, 20, 20, 200, 20, hwnd, NULL, NULL, NULL);
            hInputBox = CreateWindowEx(WS_EX_CLIENTEDGE, "EDIT", "", WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL, 20, 45, 300, 25, hwnd, (HMENU)IDC_INPUT_BARCODE, NULL, NULL);
            
            hProcessBtn = CreateWindow("BUTTON", "Process Scan", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 20, 80, 150, 30, hwnd, (HMENU)IDC_BTN_PROCESS, NULL, NULL);
            
            CreateWindow("STATIC", "2. Cadet Field (Hashed ID):", WS_CHILD | WS_VISIBLE, 20, 130, 200, 20, hwnd, NULL, NULL, NULL);
            hOutputField = CreateWindowEx(WS_EX_CLIENTEDGE, "EDIT", "", WS_CHILD | WS_VISIBLE | ES_READONLY | ES_AUTOHSCROLL, 20, 155, 450, 25, hwnd, (HMENU)IDC_OUTPUT_CADET, NULL, NULL);
            break;

        case WM_COMMAND:
            if (LOWORD(wParam) == IDC_BTN_PROCESS) {
                HandleScanEvent();
            }
            break;

        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProc(hwnd, uMsg