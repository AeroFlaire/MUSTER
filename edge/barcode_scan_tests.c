#include <stdio.h> //Gemini Pro
#include <string.h>
#include <assert.h>

// ---------------------------------------------------------
// Function Signatures (Linking to your main logic file)
// ---------------------------------------------------------
extern int ExtractDoDID(const char* rawBarcode, char* outputId);
extern int ComputeHMACSHA256(const char* inputData, const char* secretKey, char* outputHashHex);

const char* TEST_SECRET_KEY = "super_secret_muster_edipi_key_32_chars+";

// ---------------------------------------------------------
// Test Suite
// ---------------------------------------------------------

void test_ExtractDoDID() {
    char output[11];
    int result;

    // Test 1: Clean 10-digit ID embedded inside a messy string
    result = ExtractDoDID("PDF417_DATA_XYZ_1234567890_END", output);
    assert(result == 1);
    assert(strcmp(output, "1234567890") == 0);

    // Test 2: Exactly 10 digits with nothing else
    result = ExtractDoDID("0987654321", output);
    assert(result == 1);
    assert(strcmp(output, "0987654321") == 0);

    // Test 3: String with less than 10 consecutive digits (Should Fail)
    result = ExtractDoDID("ABC_123456789_XYZ", output);
    assert(result == 0); // 0 means failure to find 10 digits

    // Test 4: String with letters interrupting the numbers (Should Fail)
    result = ExtractDoDID("12345A67890", output);
    assert(result == 0);

    printf("[PASS] ExtractDoDID Tests\n");
}

void test_ComputeHMACSHA256() {
    char hash1[65];
    char hash2[65];
    char hash3[65];
    int result;

    // Test 5: Check successful hash generation
    result = ComputeHMACSHA256("1234567890", TEST_SECRET_KEY, hash1);
    assert(result == 1);
    assert(strlen(hash1) == 64); // SHA-256 hex string must be exactly 64 chars

    // Test 6: Determinism (Same input and key must produce identical hashes)
    ComputeHMACSHA256("1234567890", TEST_SECRET_KEY, hash2);
    assert(strcmp(hash1, hash2) == 0);

    // Test 7: Avalanche Effect (Different input must produce completely different hashes)
    ComputeHMACSHA256("0987654321", TEST_SECRET_KEY, hash3);
    assert(strcmp(hash1, hash3) != 0);

    printf("[PASS] ComputeHMACSHA256 Tests\n");
}

// ---------------------------------------------------------
// Test Runner
// ---------------------------------------------------------
int main() {
    printf("Running MUSTER Barcode Scanner Tests...\n");
    printf("---------------------------------------\n");

    test_ExtractDoDID();
    test_ComputeHMACSHA256();

    printf("---------------------------------------\n");
    printf("ALL TESTS PASSED SUCCESSFULLY.\n");

    return 0;
}