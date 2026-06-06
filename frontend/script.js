document.addEventListener('DOMContentLoaded', () => {
    const questionContainer = document.getElementById('questions-container');
    const addQuestionBtn = document.getElementById('add-question');
    const form = document.getElementById('orchestrator-form');

    // Elements for result updating
    const emptyState = document.getElementById('empty-state');
    const resultsContent = document.getElementById('results-content');
    const submitBtn = document.getElementById('submit-btn');
    const btnText = submitBtn.querySelector('.btn-text');
    const loader = submitBtn.querySelector('.loader');

    // Add question dynamic input
    addQuestionBtn.addEventListener('click', () => {
        const row = document.createElement('div');
        row.className = 'question-row';
        row.innerHTML = `
            <input type="text" class="policy-q" placeholder="Assorted policy question..." value="">
            <button type="button" class="btn-icon remove-q" title="Remove question">&times;</button>
        `;
        questionContainer.appendChild(row);
    });

    // Remove question
    questionContainer.addEventListener('click', (e) => {
        if (e.target.classList.contains('remove-q')) {
            e.target.parentElement.remove();
        }
    });

    // Form submission
    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const pdfFile = document.getElementById('pdf_file').files[0];
        const jobRole = document.getElementById('job_role').value;

        // Collect policy questions
        const policyQuestions = [];
        document.querySelectorAll('.policy-q').forEach(input => {
            if (input.value.trim() !== '') {
                policyQuestions.push(input.value.trim());
            }
        });

        // Set Loading state
        btnText.classList.add('hidden');
        loader.classList.remove('hidden');
        submitBtn.disabled = true;

        resultsContent.classList.add('hidden');
        emptyState.classList.add('hidden');

        const formData = new FormData();
        formData.append("pdf_file", pdfFile);
        formData.append("job_role", jobRole);
        formData.append("policy_questions", JSON.stringify(policyQuestions));

        try {
            const response = await fetch('http://localhost:9000/orchestrate', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || 'API request failed');
            }

            const data = await response.json();

            // Render Results
            renderResults(data);

            resultsContent.classList.remove('hidden');
        } catch (error) {
            console.error(error);
            alert(`Error: ${error.message}`);
            emptyState.classList.remove('hidden');
        } finally {
            // Unset loading state
            btnText.classList.remove('hidden');
            loader.classList.add('hidden');
            submitBtn.disabled = false;
        }
    });

    function renderResults(data) {
        const candidateInfo = data.candidate_info;

        // Profile Snapshot
        document.getElementById('res-score').innerText = candidateInfo.score;
        document.getElementById('res-name').innerText = candidateInfo.name || "N/A";
        document.getElementById('res-college').innerText = candidateInfo.College || "N/A";
        document.getElementById('res-cgpa').innerText = candidateInfo.CGPA || "N/A";

        // Score circle color context
        const scoreCircle = document.querySelector('.score-circle');
        scoreCircle.style.borderColor = candidateInfo.score >= 80 ? 'var(--success-color)' :
            (candidateInfo.score >= 50 ? '#f59e0b' : 'var(--danger-color)');
        scoreCircle.style.color = scoreCircle.style.borderColor;
        scoreCircle.style.boxShadow = `0 0 15px ${scoreCircle.style.borderColor}40`;

        // Tags logic
        const renderTags = (skills, containerId, extraClass = '') => {
            const cont = document.getElementById(containerId);
            cont.innerHTML = '';
            if (!skills || skills.length === 0) {
                cont.innerHTML = '<span class="tag">N/A</span>';
                return;
            }
            skills.forEach(skill => {
                const span = document.createElement('span');
                span.className = `tag ${extraClass}`;
                span.innerText = skill;
                cont.appendChild(span);
            });
        };

        renderTags(candidateInfo.Tech_skills, 'res-tech-skills');
        renderTags(candidateInfo.Soft_skills, 'res-soft-skills', 'soft');

        // Onboarding plan markdown
        const onboardingEl = document.getElementById('res-onboarding');
        const planMarkdown = data.onboarding_plan || "No plan provided.";
        // use Marked if available
        if (typeof marked !== 'undefined') {
            onboardingEl.innerHTML = marked.parse(planMarkdown);
        } else {
            onboardingEl.innerText = planMarkdown;
        }

        // Policy answers
        const policyEl = document.getElementById('res-policy-answers');
        policyEl.innerHTML = '';
        if (Object.keys(data.policy_answers).length === 0) {
            policyEl.innerHTML = '<p class="text-secondary">No policy questions were asked.</p>';
        } else {
            for (const [q, a] of Object.entries(data.policy_answers)) {
                const card = document.createElement('div');
                card.className = 'qa-card';
                card.innerHTML = `
                    <div class="q">Q: ${q}</div>
                    <div class="a">A: ${a}</div>
                `;
                policyEl.appendChild(card);
            }
        }
    }
});
