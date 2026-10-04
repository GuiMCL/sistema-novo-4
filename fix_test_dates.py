"""Fix hardcoded dates in test files."""
import re

files_to_fix = [
    'tests/test_agendamento.py',
    'tests/test_confirmacao.py',
    'tests/test_contexto.py',
    'tests/test_servico_livre.py',
]

for filepath in files_to_fix:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Replace hardcoded date with variable
    content = content.replace('"2026-08-18"', 'DATA_TESTE_FUTURA')
    content = content.replace("'2026-08-18'", 'DATA_TESTE_FUTURA')
    
    # Fix the import if needed
    if 'from tests.conftest import' in content and 'DATA_TESTE_FUTURA' not in content:
        content = content.replace(
            'from tests.conftest import ',
            'from tests.conftest import DATA_TESTE_FUTURA, '
        )
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'Fixed {filepath}')

print('All done!')