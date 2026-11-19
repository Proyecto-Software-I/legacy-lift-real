"""Migration Pack cobol-to-java@1.0: validation, compilation and dual run (no AI involved)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import cobol_java, migration_verify

DEMO = Path(__file__).resolve().parents[2] / 'demo-cobol'
WAVE_FILES = ['src/PAYROLL.cbl', 'src/INTEREST.cbl']

INTEREST = '''package legacylift.migrated;

import java.math.BigDecimal;
import java.math.RoundingMode;

/** INTEREST: subprograma de intereses (CALC-INTEREST / 2000-APPLY-RATE). */
public final class Interest {
    private static final BigDecimal ANNUAL_RATE = new BigDecimal("4.50");

    public static void calcInterest(AccountRecord record) {
        BigDecimal updated = record.netPay.setScale(2, RoundingMode.DOWN);
        BigDecimal monthly = updated.multiply(ANNUAL_RATE).divide(new BigDecimal("1200"), 2, RoundingMode.DOWN);
        record.netPay = updated.add(monthly).setScale(2, RoundingMode.DOWN);
    }
}
'''
RECORD = '''package legacylift.migrated;

import java.math.BigDecimal;

/** Copybook ACCOUNT.cpy */
public final class AccountRecord {
    long acctId;
    String holderName = "";
    BigDecimal baseSalary = BigDecimal.ZERO;
    BigDecimal bonusAmt = BigDecimal.ZERO;
    BigDecimal taxRate = BigDecimal.ZERO;
    BigDecimal netPay = BigDecimal.ZERO;
}
'''


def payroll(seniority_bonus: str = '250.00') -> str:
    return '''package legacylift.migrated;

import java.math.BigDecimal;
import java.math.RoundingMode;

/** PAYROLL: liquidación de nómina (0000-START, 1000-CALCULATE-PAY). */
public final class Payroll {
    static String pic9(BigDecimal value, int integers) {
        String text = value.setScale(2, RoundingMode.DOWN).toPlainString();
        String[] parts = text.split("\\\\.");
        return "0".repeat(integers - parts[0].length()) + parts[0] + "." + parts[1];
    }

    public static void main(String[] args) {
        AccountRecord record = new AccountRecord();
        record.acctId = 10004512L;
        record.holderName = "CARLOS MENDOZA";
        record.baseSalary = new BigDecimal("4500.00");
        record.bonusAmt = new BigDecimal("300.00");
        record.taxRate = new BigDecimal("13.00");
        BigDecimal gross = record.baseSalary;
        gross = gross.add(new BigDecimal("SENIORITY"));
        gross = gross.add(record.bonusAmt);
        BigDecimal tax = gross.multiply(record.taxRate).divide(new BigDecimal("100"), 2, RoundingMode.DOWN);
        record.netPay = gross.subtract(tax);
        Interest.calcInterest(record);
        System.out.println("EMPLEADO: " + String.format("%-30s", record.holderName) + " NETO: " + pic9(record.netPay, 7));
    }
}
'''.replace('SENIORITY', seniority_bonus)


def response(payroll_src: str) -> str:
    root = cobol_java.JAVA_ROOT
    return json.dumps({'files': [{'path': f'{root}/Payroll.java', 'content': payroll_src},
                                 {'path': f'{root}/Interest.java', 'content': INTEREST},
                                 {'path': f'{root}/AccountRecord.java', 'content': RECORD}], 'notes': 'Traducción manual de prueba'})


@pytest.fixture
def candidate(tmp_path):
    folder = tmp_path / 'candidate'
    shutil.copytree(DEMO, folder)
    return folder


def runner_available() -> bool:
    if not shutil.which('docker'):
        return False
    probe = subprocess.run(['docker', 'image', 'inspect', 'legacylift/legacy-runner:1'], capture_output=True)
    return probe.returncode == 0


def test_programs_detects_main_and_subprogram():
    progs = {p['program_id']: p for p in cobol_java.programs(DEMO, WAVE_FILES)}
    assert progs['PAYROLL']['main'] and progs['PAYROLL']['class'] == 'Payroll'
    assert not progs['INTEREST']['main'] and progs['INTEREST']['uses']
    assert cobol_java.class_name('CALC-INTEREST') == 'CalcInterest'


def test_prompt_lists_required_classes_and_copybooks(candidate):
    prompt, progs = cobol_java.build_prompt(candidate, {'id': 'wave-01', 'goal': 'Migrar', 'files': WAVE_FILES, 'acceptance_criteria': 'Misma salida'})
    assert 'Payroll.java' in prompt and 'Interest.java' in prompt and 'ACCOUNT-RECORD' in prompt
    assert 'NETO: 0004409.97' not in prompt  # the expected output is never given to the model


@pytest.mark.parametrize('bad,reason', [
    ({'path': 'src/PAYROLL.cbl', 'content': 'x'}, 'Ruta no permitida'),
    ({'path': '../evil/Payroll.java', 'content': 'package legacylift.migrated;'}, 'Ruta no permitida'),
    ({'path': cobol_java.JAVA_ROOT + '/Payroll.java', 'content': 'class Payroll {}'}, 'package'),
    ({'path': cobol_java.JAVA_ROOT + '/Payroll.java', 'content': 'package legacylift.migrated;\nimport java.net.URL;'}, 'red'),
])
def test_apply_rejects_unsafe_output(candidate, bad, reason):
    progs = cobol_java.programs(candidate, WAVE_FILES)
    with pytest.raises(ValueError, match=reason):
        cobol_java.apply(candidate, json.dumps({'files': [bad]}), progs)


def test_apply_requires_every_program_class(candidate):
    progs = cobol_java.programs(candidate, WAVE_FILES)
    only = json.dumps({'files': [{'path': cobol_java.JAVA_ROOT + '/Payroll.java', 'content': payroll()}]})
    with pytest.raises(ValueError, match='Interest.java'):
        cobol_java.apply(candidate, only, progs)


@pytest.mark.skipif(not shutil.which('javac'), reason='javac no disponible')
def test_apply_and_compile(candidate):
    progs = cobol_java.programs(candidate, WAVE_FILES)
    written = cobol_java.apply(candidate, response(payroll()), progs)
    assert len(written) == 3 and all(w.startswith(cobol_java.JAVA_ROOT) for w in written)
    assert cobol_java.compile_java(candidate)['status'] == 'passed'
    broken = candidate / cobol_java.JAVA_ROOT / 'Interest.java'
    broken.write_text(INTEREST.replace('record.netPay =', 'record.netPay = ;'))
    assert cobol_java.compile_java(candidate)['status'] == 'failed'


@pytest.mark.skipif(not runner_available(), reason='Imagen legacylift/legacy-runner:1 no construida')
def test_dual_run_equal_and_divergent(candidate, tmp_path, monkeypatch):
    monkeypatch.setenv('LEGACYLIFT_ENV', 'development')
    progs = cobol_java.programs(candidate, WAVE_FILES)
    reference = cobol_java.dual_run(DEMO, progs, run_cobol=True, run_java=False)
    assert reference['cobol']['status'] == 'passed', reference
    assert 'NETO: 0004409.97' in reference['cobol']['runs']['PAYROLL']['stdout']

    cobol_java.apply(candidate, response(payroll()), progs)
    good = cobol_java.dual_run(candidate, progs)
    assert cobol_java.compare(reference, good)[0] == 'verified_within_tests', good

    wrong = tmp_path / 'wrong'
    shutil.copytree(DEMO, wrong)
    cobol_java.apply(wrong, response(payroll('0.00')), progs)  # forgets the loyalty bonus
    bad = cobol_java.dual_run(wrong, progs)
    classification, reason, details = cobol_java.compare(reference, bad)
    assert classification == 'divergent' and not details[0]['equal']


def test_hardcoded_expected_output_is_not_evidence(candidate):
    progs = cobol_java.programs(candidate, WAVE_FILES)
    line = 'EMPLEADO: CARLOS MENDOZA                 NETO: 0004409.97'
    cheat = payroll().replace('System.out.println("EMPLEADO: "', f'System.out.println("{line}"); if (false) System.out.println("EMPLEADO: "')
    cobol_java.apply(candidate, response(cheat), progs)
    reference = {'cobol': {'status': 'passed', 'runs': {'PAYROLL': {'stdout': line + '\n', 'exit': '0'}}}}
    assert migration_verify._hardcoded_output(candidate, reference) == [line]


def test_language_migration_wave_includes_called_subprograms_and_keep_unblocks(tmp_path, monkeypatch):
    """Planning rules for COBOL waves: callee closure, copybook-only waves rejected, Keep acknowledges."""
    import io, time, zipfile
    from fastapi.testclient import TestClient
    from app.api import app
    from app import db, planning, migration_packs, pipeline
    monkeypatch.setenv('LEGACYLIFT_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('LEGACYLIFT_BOOTSTRAP_SECRET', 'bootstrap-test-secret-with-32-plus-characters')
    with TestClient(app) as c:
        token = c.post('/api/auth/register', json={'email': 'cobol@test.test', 'display_name': 'Cobol Tester', 'password': 'long-password-123456',
                                                  'bootstrap_secret': 'bootstrap-test-secret-with-32-plus-characters'}).json()['access_token']
        h = {'Authorization': 'Bearer ' + token}
        pid = c.post('/api/projects', headers=h, json={'name': 'Nomina', 'system_name': 'COBOL', 'provider': 'gemini'}).json()['id']
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            for path in DEMO.rglob('*'):
                if path.is_file():
                    z.write(path, path.relative_to(DEMO).as_posix())
        jid = c.post(f'/api/projects/{pid}/upload', headers=h, files={'file': ('c.zip', b.getvalue())}).json()['id']
        for _ in range(200):
            if db.job_get(pid, jid)['status'] not in ('queued', 'running'):
                break
            time.sleep(.02)
        for phase in ('discover', 'understand', 'assess', 'plan'):
            monkeypatch.setattr(pipeline.ai, 'generate', lambda *a, **k: '{}')
            db.consent_set(pid, db.project_get(pid)['active_snapshot_id'], 'gemini', db.project_get(pid)['owner_id'], True)
            result, details = pipeline.perform(pid, phase)
            rid = db.run_create(pid, phase, 'gemini'); db.run_complete(rid, result, details)
        data = planning.current(pid)['data']
        data['waves'] = [
            {'id': 'wave-01', 'goal': 'Conservar el copybook', 'strategy': 'Keep', 'files': ['copybooks/ACCOUNT.cpy'], 'depends_on': [],
             'priority': 1, 'risk': 'Ninguno', 'acceptance_criteria': 'Sin cambios'},
            {'id': 'wave-02', 'goal': 'Migrar PAYROLL', 'strategy': 'LanguageMigration', 'files': ['src/PAYROLL.cbl'], 'depends_on': ['wave-01'],
             'priority': 1, 'risk': 'Formato', 'acceptance_criteria': 'Misma salida'}]
        saved = c.patch(f'/api/projects/{pid}/blueprints/current', headers=h, json={'data': data}).json()
        wave = saved['data']['waves'][1]
        assert wave['files'] == ['src/PAYROLL.cbl', 'src/INTEREST.cbl'] and wave['auto_included'] == ['src/INTEREST.cbl']
        assert wave['dual_run_entry'] == ['PAYROLL']
        copybook_only = {**saved['data']['waves'][0], 'strategy': 'LanguageMigration'}
        assert not any(x['compatible'] for x in migration_packs.compatible(pid, copybook_only))
        assert c.post(f'/api/projects/{pid}/plan/decision', headers=h, json={'approved': True}).status_code == 200
        assert c.post(f'/api/projects/{pid}/blueprints/current/waves/wave-02/decision', headers=h, json={'approved': True}).status_code == 409
        assert c.post(f'/api/projects/{pid}/blueprints/current/waves/wave-01/decision', headers=h, json={'approved': True}).status_code == 200
        assert db.project_get(pid)['active_wave_id'] is None  # Keep never becomes executable
        assert c.post(f'/api/projects/{pid}/blueprints/current/waves/wave-02/decision', headers=h, json={'approved': True}).status_code == 200
        assert db.project_get(pid)['active_pack_id'] == 'cobol-to-java@1.0'
